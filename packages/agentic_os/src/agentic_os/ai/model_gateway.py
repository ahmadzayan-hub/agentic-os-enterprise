"""Model Gateway.

No agent calls a provider. Every inference passes through this gateway, which:

1. checks the kill switches,
2. resolves a logical model key via the routing rules,
3. enforces the agent contract allowlist and the data-classification ceiling,
4. checks the run and tenant budgets before spending anything,
5. executes with a circuit breaker and a bounded fallback chain,
6. records tokens, cost, latency and the substitution (if any) on the run,
7. writes a MODEL_CALL audit entry.

A request that cannot be served by any permitted model fails loudly. It is
never silently downgraded to a provider that is not allowed to see the data.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.ai.providers import ModelRequest, ModelResponse, get_provider
from agentic_os.assurance.audit import AuditEntry, AuditLedger
from agentic_os.core.context import ExecutionContext, classification_rank
from agentic_os.core.errors import (
    AgenticError,
    BudgetExceeded,
    ContractViolation,
    KillSwitchEngaged,
    PolicyDenied,
    UpstreamUnavailable,
)
from agentic_os.core.ids import utcnow
from agentic_os.core.registry import load_registries


@dataclass(slots=True)
class RoutingDecision:
    model_key: str
    provider: str
    provider_model_id: str
    reason: str
    considered: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    substituted_for: str | None = None


@dataclass(slots=True)
class GatewayResult:
    response: ModelResponse
    routing: RoutingDecision
    cost_usd: float
    attempts: int
    degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_key": self.routing.model_key,
            "provider": self.routing.provider,
            "generative": self.response.generative,
            "input_tokens": self.response.input_tokens,
            "output_tokens": self.response.output_tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": self.response.latency_ms,
            "attempts": self.attempts,
            "degraded": self.degraded,
            "routing_reason": self.routing.reason,
            "substituted_for": self.routing.substituted_for,
            "confidence": self.response.confidence,
        }


class CircuitBreaker:
    """Per-model failure counter with a time-boxed open state."""

    def __init__(self, failure_threshold: int = 5, window_seconds: int = 60, open_seconds: int = 30):
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.open_seconds = open_seconds
        self._failures: dict[str, list[float]] = {}
        self._opened_at: dict[str, float] = {}

    def is_open(self, key: str, *, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        opened = self._opened_at.get(key)
        if opened is None:
            return False
        if now - opened >= self.open_seconds:
            self._opened_at.pop(key, None)
            self._failures.pop(key, None)
            return False
        return True

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        window = [t for t in self._failures.get(key, []) if now - t < self.window_seconds]
        window.append(now)
        self._failures[key] = window
        if len(window) >= self.failure_threshold:
            self._opened_at[key] = now

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._opened_at.pop(key, None)

    def reset(self) -> None:
        self._failures.clear()
        self._opened_at.clear()


_BREAKER = CircuitBreaker()


def reset_circuit_breaker() -> None:
    _BREAKER.reset()


# ---------------------------------------------------------------------------
# Kill switches
# ---------------------------------------------------------------------------
def kill_switch_engaged(session: Session, tenant_id: str, scope: str, target: str = "") -> bool:
    row = session.execute(
        text(
            """
            SELECT engaged FROM kill_switches
            WHERE scope = :scope AND target_key = :target
              AND (tenant_id = CAST(:t AS uuid) OR tenant_id IS NULL)
            ORDER BY tenant_id NULLS LAST
            LIMIT 1
            """
        ),
        {"scope": scope, "target": target, "t": tenant_id},
    ).scalar()
    return bool(row)


def assert_ai_permitted(session: Session, ctx: ExecutionContext, model_key: str = "") -> None:
    """Raise if any kill switch blocks this inference."""
    checks = [("GLOBAL", ""), ("TENANT", "")]
    if model_key:
        checks.append(("MODEL", model_key))
    if ctx.agent is not None:
        checks.append(("AGENT", ctx.agent.agent_id))
    for scope, target in checks:
        if kill_switch_engaged(session, ctx.tenant_id, scope, target):
            raise KillSwitchEngaged(
                f"{scope} kill switch is engaged" + (f" for '{target}'" if target else ""),
                details={"scope": scope, "target": target},
            )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def _rule_matches(rule: dict[str, Any], signals: dict[str, Any]) -> bool:
    when = rule.get("when") or {}
    if not when:
        return True
    for key, expected in when.items():
        if key.endswith("_in"):
            actual = signals.get(key[:-3])
            if actual not in expected:
                return False
        elif key.endswith("_gte"):
            if float(signals.get(key[:-4], 0)) < float(expected):
                return False
        elif key.endswith("_lt"):
            if float(signals.get(key[:-3], 10**9)) >= float(expected):
                return False
        elif key.endswith("_eq"):
            if signals.get(key[:-3]) != expected:
                return False
        else:  # pragma: no cover - guarded by registry validation
            return False
    return True


def route(
    *,
    classification: str = "INTERNAL",
    task_kind: str = "general",
    complexity: float = 0.5,
    budget_remaining_pct: float = 100.0,
    allowed_models: frozenset[str] | None = None,
    residency: str = "",
    session: Session | None = None,
    tenant_id: str = "",
) -> RoutingDecision:
    """Resolve a logical model for this request.

    Rejection reasons are collected per candidate so that a failure explains
    exactly why every permitted model was unusable.
    """
    registries = load_registries()
    routing = registries.routing
    signals = {
        "classification": classification,
        "task_kind": task_kind,
        "complexity": complexity,
        "budget_remaining_pct": budget_remaining_pct,
    }

    preference: list[str] = []
    reason = "no routing rule matched"
    for rule in routing.get("rules", []):
        if _rule_matches(rule, signals):
            preference = list(rule.get("prefer", []))
            reason = rule.get("reason", rule.get("name", ""))
            break
    if not preference:
        preference = ["deterministic-local"]
        reason = "fallback to the local deterministic deployment"

    rejected: dict[str, str] = {}
    considered: list[str] = []
    for key in preference[: int(routing.get("fallback_chain_max_length", 3)) + 2]:
        considered.append(key)
        model = registries.models.get(key)
        if model is None:
            rejected[key] = "not in the model registry"
            continue
        if allowed_models is not None and key not in allowed_models:
            rejected[key] = "not permitted by the agent contract"
            continue
        if classification_rank(classification) > classification_rank(model["max_classification"]):
            rejected[key] = f"cleared only to {model['max_classification']}, request is {classification}"
            continue
        if model.get("approval_state") not in ("APPROVED",):
            rejected[key] = f"approval state is {model.get('approval_state')}"
            continue
        if residency and model.get("residency") not in (residency, "global"):
            rejected[key] = f"residency {model.get('residency')} does not satisfy {residency}"
            continue
        if _BREAKER.is_open(key):
            rejected[key] = "circuit breaker is open"
            continue
        provider = get_provider(model["provider"])
        if not provider.available():
            rejected[key] = "provider is not configured or not permitted"
            continue
        if session is not None and tenant_id and kill_switch_engaged(session, tenant_id, "MODEL", key):
            rejected[key] = "model kill switch is engaged"
            continue

        substituted = preference[0] if key != preference[0] else None
        return RoutingDecision(
            model_key=key,
            provider=model["provider"],
            provider_model_id=model["provider_model_id"],
            reason=reason,
            considered=considered,
            rejected=rejected,
            substituted_for=substituted,
        )

    raise PolicyDenied(
        "no approved model can serve this request",
        details={"considered": considered, "rejected": rejected, "classification": classification},
    )


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------
def _budget_state(session: Session, ctx: ExecutionContext) -> dict[str, float]:
    """Spend and caps for the tenant day and the current run."""
    tenant_cap = session.execute(
        text(
            "SELECT cost_cap_usd FROM budgets "
            "WHERE tenant_id = :t AND scope = 'TENANT' AND period = 'DAY' LIMIT 1"
        ),
        {"t": ctx.tenant_id},
    ).scalar()
    tenant_spend = session.execute(
        text(
            "SELECT COALESCE(sum(cost_usd), 0) FROM cost_records "
            "WHERE tenant_id = :t AND occurred_at >= date_trunc('day', now())"
        ),
        {"t": ctx.tenant_id},
    ).scalar_one()

    run_cap = session.execute(
        text(
            "SELECT cost_cap_usd FROM budgets "
            "WHERE tenant_id = :t AND scope = 'RUN' AND period = 'RUN' LIMIT 1"
        ),
        {"t": ctx.tenant_id},
    ).scalar()
    run_spend = 0.0
    if ctx.run_id:
        run_spend = float(
            session.execute(
                text("SELECT COALESCE(sum(cost_usd), 0) FROM cost_records WHERE run_id = :r"),
                {"r": ctx.run_id},
            ).scalar_one()
        )

    return {
        "tenant_cap": float(tenant_cap) if tenant_cap is not None else 0.0,
        "tenant_spend": float(tenant_spend),
        "run_cap": float(run_cap) if run_cap is not None else 0.0,
        "run_spend": run_spend,
    }


def budget_remaining_pct(state: dict[str, float]) -> float:
    caps = []
    if state["tenant_cap"] > 0:
        caps.append(max(0.0, 1 - state["tenant_spend"] / state["tenant_cap"]))
    if state["run_cap"] > 0:
        caps.append(max(0.0, 1 - state["run_spend"] / state["run_cap"]))
    return round(min(caps) * 100, 2) if caps else 100.0


def compute_cost(model: dict[str, Any], response: ModelResponse) -> float:
    return round(
        response.input_tokens / 1000 * float(model["input_cost_per_1k"])
        + response.output_tokens / 1000 * float(model["output_cost_per_1k"]),
        6,
    )


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------
class ModelGateway:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._ledger = AuditLedger(session)

    def complete(
        self,
        ctx: ExecutionContext,
        request: ModelRequest,
        *,
        classification: str = "INTERNAL",
        complexity: float = 0.5,
        allowed_models: frozenset[str] | None = None,
        residency: str = "",
        max_attempts: int = 3,
    ) -> GatewayResult:
        registries = load_registries()
        assert_ai_permitted(self._session, ctx)

        if allowed_models is None and ctx.agent is not None:
            contract = registries.agents.get(ctx.agent.agent_id)
            if contract is None:
                raise ContractViolation(f"agent '{ctx.agent.agent_id}' has no registered contract")
            allowed_models = frozenset(contract["models"]["allowed"])

        state = _budget_state(self._session, ctx)
        remaining_pct = budget_remaining_pct(state)
        if state["tenant_cap"] > 0 and state["tenant_spend"] >= state["tenant_cap"]:
            raise BudgetExceeded(
                "tenant daily cost cap reached",
                details={"cap_usd": state["tenant_cap"], "spend_usd": state["tenant_spend"]},
            )
        if state["run_cap"] > 0 and state["run_spend"] >= state["run_cap"]:
            raise BudgetExceeded(
                "run cost cap reached",
                details={"cap_usd": state["run_cap"], "spend_usd": state["run_spend"]},
            )

        attempts = 0
        last_error: AgenticError | None = None
        excluded: set[str] = set()
        degraded = False

        while attempts < max_attempts:
            attempts += 1
            permitted = None if allowed_models is None else frozenset(allowed_models - excluded)
            if permitted is not None and not permitted:
                break
            decision = route(
                classification=classification,
                task_kind=request.task_kind,
                complexity=complexity,
                budget_remaining_pct=remaining_pct,
                allowed_models=permitted,
                residency=residency,
                session=self._session,
                tenant_id=ctx.tenant_id,
            )
            model = registries.model(decision.model_key)
            provider = get_provider(decision.provider)
            started = time.perf_counter()
            try:
                response = provider.complete(request, decision.provider_model_id)
            except AgenticError as exc:
                _BREAKER.record_failure(decision.model_key)
                excluded.add(decision.model_key)
                last_error = exc
                degraded = True
                self._audit(ctx, decision, None, 0.0, outcome="FAILURE", error=exc.message)
                if not exc.retryable:
                    break
                continue

            _BREAKER.record_success(decision.model_key)
            if not response.latency_ms:
                response.latency_ms = int((time.perf_counter() - started) * 1000)
            cost = compute_cost(model, response)
            self._record_cost(ctx, decision, response, cost)
            self._audit(ctx, decision, response, cost, outcome="SUCCESS")
            return GatewayResult(
                response=response,
                routing=decision,
                cost_usd=cost,
                attempts=attempts,
                degraded=degraded or decision.substituted_for is not None,
            )

        raise UpstreamUnavailable(
            "every permitted model failed",
            details={
                "attempts": attempts,
                "excluded": sorted(excluded),
                "last_error": last_error.message if last_error else None,
            },
            correlation_id=ctx.correlation_id,
        )

    # -- persistence -------------------------------------------------------
    def _record_cost(
        self, ctx: ExecutionContext, decision: RoutingDecision, response: ModelResponse, cost: float
    ) -> None:
        self._session.execute(
            text(
                """
                INSERT INTO cost_records (tenant_id, run_id, category, provider, model_key,
                                          agent_key, input_tokens, output_tokens, cost_usd,
                                          occurred_at)
                VALUES (:t, :r, 'MODEL', :p, :m, :a, :it, :ot, :c, :ts)
                """
            ),
            {
                "t": ctx.tenant_id,
                "r": ctx.run_id or None,
                "p": decision.provider,
                "m": decision.model_key,
                "a": ctx.agent.agent_id if ctx.agent else "",
                "it": response.input_tokens,
                "ot": response.output_tokens,
                "c": cost,
                "ts": utcnow(),
            },
        )

    def _audit(
        self,
        ctx: ExecutionContext,
        decision: RoutingDecision,
        response: ModelResponse | None,
        cost: float,
        *,
        outcome: str,
        error: str = "",
    ) -> None:
        payload: dict[str, Any] = {
            "model_key": decision.model_key,
            "provider": decision.provider,
            "routing_reason": decision.reason,
            "considered": decision.considered,
            "rejected": decision.rejected,
            "substituted_for": decision.substituted_for,
            "cost_usd": cost,
        }
        if response is not None:
            payload.update(
                {
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "latency_ms": response.latency_ms,
                    "generative": response.generative,
                }
            )
        if error:
            payload["error"] = error
        self._ledger.append(
            ctx,
            AuditEntry(
                category="MODEL_CALL",
                action="model.complete",
                outcome=outcome,  # type: ignore[arg-type]
                resource_type="model",
                resource_id=decision.model_key,
                payload=payload,
            ),
        )
