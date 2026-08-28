"""Risk Engine.

Classifies a proposed action before it runs. Risk is computed from observable
factors, never asserted by the model that proposed the action, and the result
determines the autonomy level the action requires.

The scoring is deliberately simple and inspectable: each factor contributes a
bounded weight and every contribution is recorded, so an operator can see
exactly why an action was classified HIGH rather than MEDIUM.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext, classification_rank

RISK_ORDER = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

#: score >= threshold -> class
_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.75, "CRITICAL"),
    (0.50, "HIGH"),
    (0.25, "MEDIUM"),
    (0.0, "LOW"),
)

#: The minimum autonomy each risk class demands.
_REQUIRED_AUTONOMY = {"LOW": "A1", "MEDIUM": "A3", "HIGH": "A3", "CRITICAL": "A4"}

_SIDE_EFFECT_WEIGHT = {
    "READ": 0.0,
    "WRITE": 0.18,
    "DELETE": 0.42,
    "EXTERNAL": 0.38,
    "FINANCIAL": 0.5,
}

_REVERSIBILITY_WEIGHT = {"REVERSIBLE": 0.0, "PARTIAL": 0.15, "IRREVERSIBLE": 0.3}


@dataclass(slots=True)
class RiskFactor:
    name: str
    weight: float
    detail: str


@dataclass(slots=True)
class RiskAssessment:
    risk_class: str
    score: float
    required_autonomy: str
    reversibility: str
    financial_impact_usd: float
    factors: list[RiskFactor] = field(default_factory=list)

    @property
    def requires_approval(self) -> bool:
        return self.required_autonomy == "A4"

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_class": self.risk_class,
            "score": self.score,
            "required_autonomy": self.required_autonomy,
            "reversibility": self.reversibility,
            "financial_impact_usd": self.financial_impact_usd,
            "requires_approval": self.requires_approval,
            "factors": [{"name": f.name, "weight": f.weight, "detail": f.detail} for f in self.factors],
        }


@dataclass(slots=True)
class RiskInput:
    action: str
    side_effect: str = "READ"
    reversibility: str = "REVERSIBLE"
    classification: str = "INTERNAL"
    financial_impact_usd: float = 0.0
    affected_record_count: int = 0
    confidence: float | None = None
    injection_detected: bool = False
    origin_trust_tier: str = "SYSTEM_TRUSTED"
    tool_implementation_status: str = "IMPLEMENTED"
    safety_critical: bool = False
    external_recipients: int = 0


def assess(inp: RiskInput) -> RiskAssessment:
    """Classify an action. Pure function — no I/O, fully testable."""
    factors: list[RiskFactor] = []

    weight = _SIDE_EFFECT_WEIGHT.get(inp.side_effect, 0.3)
    if weight:
        factors.append(RiskFactor("side_effect", weight, f"side effect is {inp.side_effect}"))

    weight = _REVERSIBILITY_WEIGHT.get(inp.reversibility, 0.2)
    if weight:
        factors.append(RiskFactor("reversibility", weight, f"action is {inp.reversibility}"))

    if inp.financial_impact_usd > 0:
        # Log-scaled: 100 USD differs meaningfully from 0, and 100k from 10k,
        # but 100k and 110k do not.
        weight = min(0.4, math.log10(1 + inp.financial_impact_usd) / 15)
        factors.append(
            RiskFactor("financial_impact", round(weight, 3), f"{inp.financial_impact_usd:.2f} USD")
        )

    rank = classification_rank(inp.classification)
    if rank >= 2:
        factors.append(RiskFactor("classification", 0.1 * (rank - 1), f"data is {inp.classification}"))

    if inp.affected_record_count > 100:
        weight = min(0.2, math.log10(inp.affected_record_count) / 25)
        factors.append(RiskFactor("blast_radius", round(weight, 3), f"{inp.affected_record_count} records"))

    if inp.confidence is not None and inp.confidence < 0.7:
        weight = round(0.25 * (0.7 - inp.confidence) / 0.7, 3)
        factors.append(RiskFactor("low_confidence", weight, f"confidence {inp.confidence:.2f}"))

    if inp.injection_detected:
        factors.append(RiskFactor("prompt_injection", 0.45, "injection indicators in the source context"))

    if inp.origin_trust_tier in (
        "EXTERNAL",
        "UNTRUSTED_UPLOAD",
        "TOOL_GENERATED",
        "MODEL_GENERATED",
    ):
        factors.append(RiskFactor("untrusted_origin", 0.15, f"originates from {inp.origin_trust_tier}"))

    if inp.external_recipients > 0:
        factors.append(
            RiskFactor("external_disclosure", 0.2, f"{inp.external_recipients} external recipients")
        )

    if inp.safety_critical:
        factors.append(RiskFactor("safety_critical", 0.5, "action affects a safety-related system"))

    if inp.tool_implementation_status == "NOT_IMPLEMENTED":
        factors.append(RiskFactor("unimplemented_capability", 0.1, "target capability is not implemented"))

    score = round(min(1.0, sum(f.weight for f in factors)), 4)
    risk_class = next(name for threshold, name in _THRESHOLDS if score >= threshold)
    required = _REQUIRED_AUTONOMY[risk_class]

    # Hard floors. These are not scores to be outweighed: an irreversible or
    # money-moving action is consequential regardless of how confident anything
    # is, and injected context never gets to act autonomously.
    if inp.reversibility == "IRREVERSIBLE":
        required = "A4"
        risk_class = max(risk_class, "HIGH", key=RISK_ORDER.index)
    if inp.injection_detected:
        required = "A4"
        risk_class = max(risk_class, "HIGH", key=RISK_ORDER.index)
    if inp.side_effect == "FINANCIAL" or inp.safety_critical:
        required, risk_class = "A4", "CRITICAL"

    return RiskAssessment(
        risk_class=risk_class,
        score=score,
        required_autonomy=required,
        reversibility=inp.reversibility,
        financial_impact_usd=inp.financial_impact_usd,
        factors=factors,
    )


def record(
    session: Session,
    ctx: ExecutionContext,
    assessment: RiskAssessment,
    *,
    action: str,
    run_id: str | None = None,
    run_step_id: str | None = None,
) -> str:
    row = session.execute(
        text(
            """
            INSERT INTO risk_assessments (tenant_id, run_id, run_step_id, action, risk_class,
                                          risk_score, factors, reversibility,
                                          financial_impact_usd, required_autonomy)
            VALUES (:t, :run, :step, :action, CAST(:rc AS risk_class), :score,
                    CAST(:factors AS jsonb), :rev, :fin, CAST(:aut AS autonomy_level))
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id,
            "run": run_id or (ctx.run_id or None),
            "step": run_step_id,
            "action": action,
            "rc": assessment.risk_class,
            "score": assessment.score,
            "factors": json.dumps(
                [{"name": f.name, "weight": f.weight, "detail": f.detail} for f in assessment.factors]
            ),
            "rev": assessment.reversibility,
            "fin": assessment.financial_impact_usd,
            "aut": assessment.required_autonomy,
        },
    ).one()
    return str(row.id)


def from_tool(tool: dict[str, Any], **overrides: Any) -> RiskInput:
    """Build a risk input from a tool registry entry plus call-site signals."""
    base: dict[str, Any] = {
        "action": tool.get("tool_key") or tool.get("key", ""),
        "side_effect": tool.get("side_effect", "READ"),
        "reversibility": tool.get("reversibility", "REVERSIBLE"),
        "classification": tool.get("max_classification", "INTERNAL"),
        "tool_implementation_status": tool.get("implementation_status", "IMPLEMENTED"),
    }
    base.update(overrides)
    return RiskInput(**base)
