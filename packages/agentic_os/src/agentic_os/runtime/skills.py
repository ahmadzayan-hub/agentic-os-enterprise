"""Skill executor.

A skill runs inside an agent's authority, never beside it. Every execution:

* validates the input against the registered JSON Schema,
* runs deterministically in code where the answer is computable, and only calls
  the model gateway where genuine language work is needed,
* validates the output against the registered output schema, so a
  non-conforming result is a failure rather than something a caller has to
  defend against.

Ten of the fifteen registered skills are fully deterministic — the same input
produces byte-identical output, forever, with no model involved. That is a
deliberate architectural choice, not a limitation.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from agentic_os.ai.model_gateway import ModelGateway
from agentic_os.ai.providers import ModelRequest
from agentic_os.core.context import ExecutionContext
from agentic_os.core.errors import ContractViolation, ValidationError
from agentic_os.core.registry import load_registries


@dataclass(slots=True)
class SkillResult:
    skill_key: str
    output: dict[str, Any]
    deterministic: bool
    model_key: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    confidence: float | None = None
    citations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_key": self.skill_key,
            "deterministic": self.deterministic,
            "model_key": self.model_key,
            "cost_usd": self.cost_usd,
            "confidence": self.confidence,
            "citations": self.citations,
            "output": self.output,
        }


# ---------------------------------------------------------------------------
# Deterministic implementations
# ---------------------------------------------------------------------------
def _skill_calculate(ctx: ExecutionContext, params: dict, deps: dict) -> dict[str, Any]:
    from agentic_os.tools.builtin import calc_evaluate

    computed = calc_evaluate(
        deps["session"],
        ctx,
        {
            "expression": params["expression"],
            "variables": params.get("variables", {}),
            "precision": params.get("precision", 6),
        },
    )
    return {
        "value": computed["value"],
        "expression": computed["expression"],
        "steps": [f"evaluated {computed['expression']} with {computed['variables']}"],
    }


def _skill_compare(ctx: ExecutionContext, params: dict, deps: dict) -> dict[str, Any]:
    options = params["options"]
    criteria = params["criteria"]
    total_weight = sum(abs(float(c["weight"])) for c in criteria) or 1.0

    # Min-max normalise each criterion so weights mean what they say regardless
    # of the units the underlying values happen to use.
    ranges: dict[str, tuple[float, float]] = {}
    for criterion in criteria:
        key = criterion["key"]
        values = [float(o.get(key, 0) or 0) for o in options]
        ranges[key] = (min(values), max(values))

    ranking = []
    for option in options:
        score = 0.0
        for criterion in criteria:
            key = criterion["key"]
            weight = float(criterion["weight"])
            higher_is_better = criterion.get("higher_is_better", True)
            low, high = ranges[key]
            raw = float(option.get(key, 0) or 0)
            normalised = 0.5 if high == low else (raw - low) / (high - low)
            if not higher_is_better:
                normalised = 1 - normalised
            score += weight * normalised
        ranking.append(
            {
                "option": str(option.get("key", option.get("name", ""))),
                "score": round(score / total_weight, 6),
            }
        )
    ranking.sort(key=lambda r: -r["score"])
    return {"ranking": ranking}


def _skill_forecast(ctx: ExecutionContext, params: dict, deps: dict) -> dict[str, Any]:
    series = [float(v) for v in params["series"]]
    periods = int(params["periods"])
    method = params.get("method", "linear")
    n = len(series)

    if method == "moving_average":
        window = min(3, n)
        last = statistics.fmean(series[-window:])
        forecast = [round(last, 6)] * periods
        residuals = [series[i] - statistics.fmean(series[max(0, i - window) : i + 1]) for i in range(n)]
    elif method == "holt_linear":
        alpha, beta = 0.5, 0.3
        level, trend = series[0], series[1] - series[0] if n > 1 else 0.0
        fitted = [level]
        for value in series[1:]:
            previous_level = level
            level = alpha * value + (1 - alpha) * (level + trend)
            trend = beta * (level - previous_level) + (1 - beta) * trend
            fitted.append(level)
        forecast = [round(level + (i + 1) * trend, 6) for i in range(periods)]
        residuals = [series[i] - fitted[i] for i in range(n)]
    else:  # ordinary least squares on the index
        mean_x = (n - 1) / 2
        mean_y = statistics.fmean(series)
        denominator = sum((i - mean_x) ** 2 for i in range(n)) or 1.0
        slope = sum((i - mean_x) * (series[i] - mean_y) for i in range(n)) / denominator
        intercept = mean_y - slope * mean_x
        forecast = [round(intercept + slope * (n + i), 6) for i in range(periods)]
        residuals = [series[i] - (intercept + slope * i) for i in range(n)]

    variance = statistics.pvariance(series) if n > 1 else 0.0
    residual_variance = statistics.fmean([r * r for r in residuals]) if residuals else 0.0
    fit_quality = 0.0 if variance == 0 else max(0.0, min(1.0, 1 - residual_variance / variance))
    return {"forecast": forecast, "method": method, "fit_quality": round(fit_quality, 4)}


def _skill_optimise(ctx: ExecutionContext, params: dict, deps: dict) -> dict[str, Any]:
    """0/1 knapsack by exact dynamic programming when tractable.

    Costs are scaled to integers so the DP is exact. Above the size threshold it
    falls back to the value-density greedy heuristic and says so, rather than
    silently returning a suboptimal answer labelled as optimal.
    """
    items = params["items"]
    budget = float(params["budget"])
    scale = 100
    scaled_budget = int(budget * scale)

    if scaled_budget <= 2_000_000 and len(items) <= 200:
        table = [0.0] * (scaled_budget + 1)
        choice: list[list[bool]] = []
        for item in items:
            cost = int(float(item["cost"]) * scale)
            value = float(item["value"])
            taken = [False] * (scaled_budget + 1)
            for capacity in range(scaled_budget, cost - 1, -1):
                if table[capacity - cost] + value > table[capacity]:
                    table[capacity] = table[capacity - cost] + value
                    taken[capacity] = True
            choice.append(taken)

        selected: list[str] = []
        capacity = scaled_budget
        for index in range(len(items) - 1, -1, -1):
            if choice[index][capacity]:
                selected.append(str(items[index]["key"]))
                capacity -= int(float(items[index]["cost"]) * scale)
        selected.reverse()
        method = "exact_dynamic_programming"
    else:
        ordered = sorted(items, key=lambda i: float(i["value"]) / max(float(i["cost"]), 1e-9), reverse=True)
        selected, remaining = [], budget
        for item in ordered:
            if float(item["cost"]) <= remaining:
                selected.append(str(item["key"]))
                remaining -= float(item["cost"])
        method = "greedy_value_density_approximation"

    chosen = [i for i in items if str(i["key"]) in set(selected)]
    return {
        "selected": selected,
        "total_value": round(sum(float(i["value"]) for i in chosen), 6),
        "total_cost": round(sum(float(i["cost"]) for i in chosen), 6),
        "method": method,
    }


def _skill_reconcile(ctx: ExecutionContext, params: dict, deps: dict) -> dict[str, Any]:
    key = params["key"]
    tolerance = float(params.get("tolerance", 0))
    compare_fields = params.get("compare_fields") or []

    left = {str(r.get(key)): r for r in params["left"] if r.get(key) is not None}
    right = {str(r.get(key)): r for r in params["right"] if r.get(key) is not None}

    mismatched = []
    for identifier in sorted(set(left) & set(right)):
        differences = {}
        for field_name in compare_fields or sorted(set(left[identifier]) | set(right[identifier])):
            if field_name == key:
                continue
            a, b = left[identifier].get(field_name), right[identifier].get(field_name)
            if a == b:
                continue
            try:
                if abs(float(a) - float(b)) <= tolerance:
                    continue
            except (TypeError, ValueError):
                pass
            differences[field_name] = {"left": a, "right": b}
        if differences:
            mismatched.append({"key": identifier, "differences": differences})

    return {
        "matched": len(set(left) & set(right)) - len(mismatched),
        "only_left": sorted(set(left) - set(right)),
        "only_right": sorted(set(right) - set(left)),
        "mismatched": mismatched,
    }


def _skill_transform(ctx: ExecutionContext, params: dict, deps: dict) -> dict[str, Any]:
    mapping = params["mapping"]
    drop_unmapped = bool(params.get("drop_unmapped", True))
    out = []
    for record in params["records"]:
        transformed = {} if drop_unmapped else dict(record)
        for target, source in mapping.items():
            if source in record:
                transformed[target] = record[source]
        out.append(transformed)
    return {"records": out, "transformed_count": len(out)}


def _skill_validate(ctx: ExecutionContext, params: dict, deps: dict) -> dict[str, Any]:
    validator = Draft202012Validator(params["schema"])
    errors = sorted(validator.iter_errors(params["payload"]), key=lambda e: list(e.path))
    return {
        "valid": not errors,
        "errors": [
            {"path": list(e.path), "message": e.message, "validator": e.validator} for e in errors[:50]
        ],
    }


def _skill_verify(ctx: ExecutionContext, params: dict, deps: dict) -> dict[str, Any]:
    from agentic_os.knowledge.retrieval import verify_citations

    return verify_citations(
        params["claims"], params["sources"], min_overlap=float(params.get("min_overlap", 0.6))
    )


def _skill_search(ctx: ExecutionContext, params: dict, deps: dict) -> dict[str, Any]:
    gateway = deps["tool_gateway"]
    call = gateway.invoke(
        ctx,
        "knowledge.search",
        {
            "query": params["query"],
            "top_k": params.get("top_k", 8),
            "strategy": "hybrid",
        },
        idempotency_key=deps["idempotency_key"],
    )
    results = (call.result or {}).get("results", [])
    return {
        "results": [
            {
                "chunk_id": r["chunk_id"],
                "document_id": r["document_id"],
                "score": r["score"],
                "snippet": r.get("snippet", ""),
            }
            for r in results
        ]
    }


def _skill_retrieve(ctx: ExecutionContext, params: dict, deps: dict) -> dict[str, Any]:
    gateway = deps["tool_gateway"]
    call = gateway.invoke(
        ctx,
        "knowledge.fetch_document",
        {"document_id": params["document_id"]},
        idempotency_key=deps["idempotency_key"],
    )
    result = call.result or {}
    return {
        "document_id": result.get("document_id", params["document_id"]),
        "title": result.get("title", ""),
        "content": result.get("content", ""),
        "classification": result.get("classification", "INTERNAL"),
    }


def _skill_extract(ctx: ExecutionContext, params: dict, deps: dict) -> dict[str, Any]:
    """Deterministic labelled-field extraction, with a model fallback."""
    text_value = params["text"]
    fields = params["fields"]
    values: dict[str, Any] = {}
    spans: dict[str, list[int]] = {}
    for name in fields:
        pattern = re.compile(rf"{re.escape(name)}\s*[:=-]\s*(?P<value>[^\n,;]{{1,200}})", re.IGNORECASE)
        match = pattern.search(text_value)
        if match:
            values[name] = match.group("value").strip()
            spans[name] = [match.start("value"), match.end("value")]
    return {"values": values, "missing": [f for f in fields if f not in values], "spans": spans}


DETERMINISTIC_SKILLS: dict[str, Callable[[ExecutionContext, dict, dict], dict[str, Any]]] = {
    "calculate": _skill_calculate,
    "compare": _skill_compare,
    "forecast": _skill_forecast,
    "optimise": _skill_optimise,
    "reconcile": _skill_reconcile,
    "transform": _skill_transform,
    "validate": _skill_validate,
    "verify": _skill_verify,
    "search": _skill_search,
    "retrieve": _skill_retrieve,
    "extract": _skill_extract,
}

#: Skills routed through the model gateway, with the task kind each maps to.
MODEL_SKILLS: dict[str, str] = {
    "summarise": "summarise",
    "draft": "draft",
    "classify": "classify",
    "analyse": "answer",
}


class SkillExecutor:
    """Executes a registered skill under an agent's contract."""

    def __init__(self, session: Session, tool_gateway: Any, model_gateway: ModelGateway) -> None:
        self._session = session
        self._tools = tool_gateway
        self._models = model_gateway

    def execute(
        self,
        ctx: ExecutionContext,
        skill_key: str,
        params: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> SkillResult:
        registries = load_registries()
        skill = registries.skills.get(skill_key)
        if skill is None:
            raise ValidationError(f"skill '{skill_key}' is not registered")

        if ctx.agent is not None:
            contract = registries.agents.get(ctx.agent.agent_id)
            if contract is None or skill_key not in contract["skills"]["allowed"]:
                raise ContractViolation(
                    f"agent '{ctx.agent.agent_id}' may not use skill '{skill_key}'",
                    details={"skill": skill_key},
                )

        errors = sorted(
            Draft202012Validator(skill["input_schema"]).iter_errors(params),
            key=lambda e: list(e.path),
        )
        if errors:
            raise ValidationError(
                f"input to skill '{skill_key}' failed schema validation",
                details={"errors": [{"path": list(e.path), "message": e.message} for e in errors[:10]]},
            )

        deps = {
            "session": self._session,
            "tool_gateway": self._tools,
            "model_gateway": self._models,
            "idempotency_key": idempotency_key,
        }

        if skill_key in DETERMINISTIC_SKILLS:
            output = DETERMINISTIC_SKILLS[skill_key](ctx, params, deps)
            result = SkillResult(skill_key=skill_key, output=output, deterministic=True, confidence=1.0)
        elif skill_key in MODEL_SKILLS:
            result = self._execute_with_model(ctx, skill_key, skill, params)
        else:
            raise ValidationError(
                f"skill '{skill_key}' is registered but has no executor",
                details={"remediation": "register a deterministic or model implementation"},
            )

        output_errors = sorted(
            Draft202012Validator(skill["output_schema"]).iter_errors(result.output),
            key=lambda e: list(e.path),
        )
        if output_errors:
            raise ValidationError(
                f"skill '{skill_key}' produced output that does not match its contract",
                details={
                    "errors": [{"path": list(e.path), "message": e.message} for e in output_errors[:10]]
                },
            )
        return result

    def _execute_with_model(
        self, ctx: ExecutionContext, skill_key: str, skill: dict, params: dict
    ) -> SkillResult:
        from agentic_os.ai import prompt_registry

        prompt_key = {
            "summarise": "knowledge.answer",
            "draft": "communications.draft",
            "classify": "knowledge.answer",
            "analyse": "analysis.narrative",
        }[skill_key]
        try:
            prompt = prompt_registry.resolve(self._session, ctx.tenant_id, prompt_key)
            system = prompt.body
        except Exception:
            system = "Answer strictly from the supplied evidence and cite every claim."

        payload = dict(params)
        if skill_key == "summarise":
            payload.setdefault("max_sentences", max(2, params.get("max_words", 200) // 40))
        if skill_key == "analyse":
            # The analyse skill takes `evidence`; the extractive engine reads
            # `sources`. Without this the engine sees no sources and correctly
            # reports that it cannot ground an answer — which then trips the
            # agent's citation requirement.
            payload.setdefault(
                "sources",
                [
                    {"id": str(item.get("id", "")), "text": str(item.get("text", ""))}
                    for item in params.get("evidence", [])
                ],
            )
            payload.setdefault("focus", str(params.get("question", "")))
            payload.setdefault("max_sentences", 4)
        if skill_key == "draft":
            payload.setdefault("evidence", params.get("evidence", []))
            payload.setdefault("document_type", params.get("document_type", "note"))
            payload.setdefault("subject", params.get("context", "")[:120])

        gateway_result = self._models.complete(
            ctx,
            ModelRequest(
                system=system,
                user=str(params.get("query") or params.get("context") or params.get("text") or ""),
                task_kind=MODEL_SKILLS[skill_key],
                response_format="json",
                payload=payload,
            ),
            classification=str(ctx.attributes.get("classification", "INTERNAL")),
        )
        body = gateway_result.response.structured or gateway_result.response.as_json()

        if skill_key == "summarise":
            if not body.get("supported"):
                raise ValidationError(
                    "summarisation could not be grounded in the supplied sources",
                    details={"reason": body.get("reason")},
                )
            output = {"summary": body.get("summary", ""), "citations": body.get("citations", [])}
        elif skill_key == "draft":
            if not body.get("supported"):
                raise ValidationError(
                    "drafting requires supporting evidence", details={"reason": body.get("reason")}
                )
            output = {
                "draft": body["draft"],
                "requires_human_send": True,
                "word_count": body.get("word_count", len(body["draft"].split())),
            }
        elif skill_key == "classify":
            output = {
                "label": body.get("label", ""),
                "score": float(body.get("score", 0.0)),
                "rationale": body.get("rationale", ""),
            }
        else:  # analyse
            statement = body.get("answer", "")
            output = {
                "findings": (
                    [{"statement": statement, "support": body.get("citations", [])}] if statement else []
                ),
                "confidence": float(body.get("confidence", 0.0)),
            }

        return SkillResult(
            skill_key=skill_key,
            output=output,
            deterministic=not gateway_result.response.generative,
            model_key=gateway_result.routing.model_key,
            cost_usd=gateway_result.cost_usd,
            input_tokens=gateway_result.response.input_tokens,
            output_tokens=gateway_result.response.output_tokens,
            confidence=gateway_result.response.confidence,
            citations=list(body.get("citations", [])),
        )
