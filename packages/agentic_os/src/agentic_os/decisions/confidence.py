"""Confidence, computed from countable inputs or not reported at all.

The brief is blunt about this and it is right to be: an invented confidence
percentage is the most damaging thing a decision product can display. It looks
authoritative, it cannot be falsified by the person reading it, and it will be
quoted in a meeting as though it meant something.

So there is exactly one way to obtain a figure here, and it needs four things
that can be counted:

===================  ==========================================================
Evidence count       How much evidence is linked, saturating at five. One
                     source is not corroboration.
Evidence recency     What fraction of it was observed inside the freshness
                     window. A decision resting on year-old readings is a
                     decision about last year.
Source authority     The mean authority weight of the linked sources. A primary
                     record and a hallway conversation are not the same input.
Option separation    The score gap between the best and second-best option. If
                     two options score alike, the recommendation is a coin
                     toss however good the evidence is.
===================  ==========================================================

When the inputs cannot support a figure — no evidence, or fewer than two
options to separate — the answer is ``None``, and every surface renders
"Confidence: Not Calculated". Not zero, which reads as *certainly wrong*; not a
floor value, which is an invented number wearing a modest hat.

Every returned figure carries the inputs that produced it. The database
enforces this too: ``recommendations`` refuses a non-null confidence whose
calculation has no inputs array, so a figure cannot be stored without the means
to reconstruct and challenge it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

#: How each input contributes. Stated here, once, and written into every
#: calculation record so a stored figure is reproducible from its own row.
WEIGHTS: dict[str, float] = {
    "evidence_count": 0.30,
    "evidence_recency": 0.20,
    "source_authority": 0.25,
    "option_separation": 0.25,
}

#: Evidence beyond this adds no further confidence. Corroboration has
#: diminishing returns and an unbounded count would let volume masquerade as
#: rigour.
EVIDENCE_SATURATION = 5

#: Evidence observed within this window counts as current.
FRESHNESS_WINDOW = timedelta(days=90)

#: Below this many evidence items, or this many options, no figure is produced.
MINIMUM_EVIDENCE = 1
MINIMUM_OPTIONS = 2


@dataclass(slots=True)
class ConfidenceInput:
    name: str
    raw: float
    normalised: float
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "raw": round(self.raw, 4),
            "normalised": round(self.normalised, 4),
            "weight": self.weight,
        }


@dataclass(slots=True)
class ConfidenceResult:
    """A confidence figure, or a stated reason there isn't one."""

    value: float | None
    inputs: list[ConfidenceInput] = field(default_factory=list)
    reason: str = ""

    @property
    def is_calculated(self) -> bool:
        return self.value is not None

    def display(self) -> str:
        """What the user sees. The words matter — this is the contract."""
        if self.value is None:
            return "Not Calculated"
        return f"{self.value * 100:.0f}%"

    def calculation(self) -> dict[str, Any]:
        """The record stored alongside the figure.

        Note the shape: ``inputs`` is a non-empty list exactly when ``value`` is
        not None, which is what the database CHECK constraint tests.
        """
        return {
            "method": "weighted_inputs_v1",
            "weights": WEIGHTS,
            "inputs": [i.to_dict() for i in self.inputs],
            "reason": self.reason,
        }


def _no_figure(reason: str) -> ConfidenceResult:
    return ConfidenceResult(value=None, inputs=[], reason=reason)


def calculate_confidence(
    session: Session,
    *,
    tenant_id: str,
    decision_id: str,
    now: datetime | None = None,
) -> ConfidenceResult:
    """Compute a confidence for a decision's recommendation, or decline to.

    Reads through the caller's own session, so row level security applies: a
    confidence is computed from the evidence the caller can actually see, and
    never from evidence they cannot.
    """
    moment = now or datetime.now(UTC)

    evidence = (
        session.execute(
            text(
                "SELECT authority_weight, observed_at FROM decision_evidence "
                "WHERE tenant_id = CAST(:t AS uuid) AND decision_id = CAST(:d AS uuid)"
            ),
            {"t": tenant_id, "d": decision_id},
        )
        .mappings()
        .all()
    )
    if len(evidence) < MINIMUM_EVIDENCE:
        return _no_figure("no evidence is linked to this decision")

    scores = [
        float(r["score"])
        for r in session.execute(
            text(
                "SELECT score FROM decision_options "
                "WHERE tenant_id = CAST(:t AS uuid) AND decision_id = CAST(:d AS uuid) "
                "AND score IS NOT NULL ORDER BY score DESC"
            ),
            {"t": tenant_id, "d": decision_id},
        )
        .mappings()
        .all()
    ]
    if len(scores) < MINIMUM_OPTIONS:
        return _no_figure("fewer than two scored options, so there is no separation to measure")

    count_raw = float(len(evidence))
    count_norm = min(count_raw, EVIDENCE_SATURATION) / EVIDENCE_SATURATION

    fresh = sum(1 for r in evidence if _age(r["observed_at"], moment) <= FRESHNESS_WINDOW)
    recency_norm = fresh / len(evidence)

    authority_raw = sum(float(r["authority_weight"]) for r in evidence) / len(evidence)

    separation_raw = scores[0] - scores[1]

    inputs = [
        ConfidenceInput("evidence_count", count_raw, count_norm, WEIGHTS["evidence_count"]),
        ConfidenceInput("evidence_recency", float(fresh), recency_norm, WEIGHTS["evidence_recency"]),
        ConfidenceInput("source_authority", authority_raw, authority_raw, WEIGHTS["source_authority"]),
        ConfidenceInput("option_separation", separation_raw, separation_raw, WEIGHTS["option_separation"]),
    ]
    value = sum(i.normalised * i.weight for i in inputs)
    # Clamped only against floating point drift, never to impose a floor.
    value = max(0.0, min(1.0, value))
    return ConfidenceResult(value=round(value, 4), inputs=inputs, reason="")


def _age(observed_at: Any, moment: datetime) -> timedelta:
    if not isinstance(observed_at, datetime):
        # An unparseable timestamp counts as stale rather than fresh: the safe
        # direction is the one that lowers confidence.
        return FRESHNESS_WINDOW * 2
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    return moment - observed_at
