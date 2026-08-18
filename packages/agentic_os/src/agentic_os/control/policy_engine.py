"""Policy Decision Point.

Policies are declarative rules loaded from the registry and cached per tenant.
Evaluation is deterministic and total: the engine returns exactly one effect
for a request, and always records why.

Combination is deny-overrides with an explicit default:

* any matching DENY wins outright;
* otherwise any matching REQUIRE_APPROVAL wins;
* otherwise a matching ALLOW permits the action;
* if nothing matched, the default is DENY — an action no policy contemplates
  is not permitted.

MONITOR rules never change the outcome; they attach an obligation so the action
is recorded for review. A policy whose enforcement is MONITOR has its DENY and
REQUIRE_APPROVAL effects downgraded the same way, which is how a new policy is
rolled out without breaking production on day one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.config import get_settings
from agentic_os.core.context import ExecutionContext
from agentic_os.core.ids import prefixed_id

Effect = str  # ALLOW | DENY | REQUIRE_APPROVAL | MONITOR


@dataclass(slots=True)
class PolicyRequest:
    """The subject/action/resource triple plus every attribute rules may test."""

    action: str
    resource: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    def signals(self, ctx: ExecutionContext) -> dict[str, Any]:
        base: dict[str, Any] = {
            "action": self.action,
            "resource": self.resource,
            "principal_authenticated": ctx.human is not None or bool(ctx.service_principal),
            "agent": ctx.agent.agent_id if ctx.agent else "",
            "autonomy_level": ctx.agent.autonomy_level if ctx.agent else "",
            "environment": ctx.environment,
        }
        base.update(self.attributes)
        return base


@dataclass(slots=True)
class MatchedRule:
    policy_key: str
    rule_name: str
    effect: Effect
    reason: str
    enforcement: str
    obligations: tuple[str, ...] = ()
    downgraded_from: str | None = None


@dataclass(slots=True)
class PolicyDecision:
    effect: Effect
    reason: str
    matched: list[MatchedRule] = field(default_factory=list)
    obligations: tuple[str, ...] = ()
    decision_id: str = field(default_factory=lambda: prefixed_id("pdp"))

    @property
    def allowed(self) -> bool:
        return self.effect in ("ALLOW", "MONITOR")

    @property
    def requires_approval(self) -> bool:
        return self.effect == "REQUIRE_APPROVAL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "effect": self.effect,
            "reason": self.reason,
            "obligations": list(self.obligations),
            "matched": [
                {
                    "policy": m.policy_key,
                    "rule": m.rule_name,
                    "effect": m.effect,
                    "reason": m.reason,
                    "enforcement": m.enforcement,
                    "downgraded_from": m.downgraded_from,
                }
                for m in self.matched
            ],
        }


# ---------------------------------------------------------------------------
# Condition matching
# ---------------------------------------------------------------------------
_SUFFIX_OPS = ("_in", "_not_in", "_gte", "_gt", "_lte", "_lt", "_eq", "_ne", "_contains")


def _condition_matches(condition: dict[str, Any], signals: dict[str, Any]) -> bool:
    """All keys must match. An unknown signal never matches — fail closed."""
    for key, expected in condition.items():
        suffix = next((s for s in _SUFFIX_OPS if key.endswith(s)), None)
        if suffix is None:
            field_name, op = key, "_eq"
        else:
            field_name, op = key[: -len(suffix)], suffix

        if field_name not in signals:
            return False
        actual = signals[field_name]

        try:
            if op == "_in":
                if actual not in expected:
                    return False
            elif op == "_not_in":
                if actual in expected:
                    return False
            elif op == "_eq":
                if actual != expected:
                    return False
            elif op == "_ne":
                if actual == expected:
                    return False
            elif op == "_contains":
                if expected not in (actual or []):
                    return False
            elif op == "_gte":
                if float(actual) < float(expected):
                    return False
            elif op == "_gt":
                if float(actual) <= float(expected):
                    return False
            elif op == "_lte":
                if float(actual) > float(expected):
                    return False
            elif op == "_lt":
                if float(actual) >= float(expected):
                    return False
        except (TypeError, ValueError):
            # A malformed comparison is a non-match, never an accidental allow.
            return False
    return True


class PolicyEngine:
    """Evaluates policy for one tenant, reading rules from the database."""

    _EFFECT_PRIORITY = {"DENY": 3, "REQUIRE_APPROVAL": 2, "ALLOW": 1, "MONITOR": 0}

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._policies: list[dict[str, Any]] | None = None

    def _load(self) -> list[dict[str, Any]]:
        if self._policies is not None:
            return self._policies
        rows = self._session.execute(
            text(
                """
                SELECT p.policy_key, p.enforcement, p.status, pv.rules
                FROM policies p
                JOIN policy_versions pv
                  ON pv.policy_id = p.id AND pv.version = p.current_version
                WHERE p.tenant_id = :t AND p.status = 'ACTIVE'
                  AND pv.effective_from <= now()
                ORDER BY p.policy_key
                """
            ),
            {"t": self._tenant_id},
        ).mappings().all()
        self._policies = [
            {
                "key": r["policy_key"],
                "enforcement": r["enforcement"],
                "rules": r["rules"] if isinstance(r["rules"], list) else json.loads(r["rules"]),
            }
            for r in rows
        ]
        return self._policies

    def evaluate(self, ctx: ExecutionContext, request: PolicyRequest) -> PolicyDecision:
        signals = request.signals(ctx)
        global_mode = get_settings().policy_mode
        matched: list[MatchedRule] = []

        for policy in self._load():
            enforcement = policy["enforcement"]
            for rule in policy["rules"]:
                if not _condition_matches(rule.get("when") or {}, signals):
                    continue
                effect = rule["effect"]
                downgraded_from = None
                if (enforcement == "MONITOR" or global_mode == "monitor") and effect in (
                    "DENY",
                    "REQUIRE_APPROVAL",
                ):
                    downgraded_from, effect = effect, "MONITOR"
                matched.append(
                    MatchedRule(
                        policy_key=policy["key"],
                        rule_name=rule.get("name", "unnamed"),
                        effect=effect,
                        reason=rule.get("reason", ""),
                        enforcement=enforcement,
                        obligations=tuple(rule.get("obligations", [])),
                        downgraded_from=downgraded_from,
                    )
                )
                # First matching rule within a policy decides for that policy.
                break

        if not matched:
            return PolicyDecision(
                effect="DENY",
                reason=(
                    f"no policy permits action '{request.action}'; the platform default "
                    "is deny"
                ),
            )

        winner = max(matched, key=lambda m: self._EFFECT_PRIORITY[m.effect])
        obligations: list[str] = []
        for rule in matched:
            obligations.extend(rule.obligations)
            if rule.downgraded_from:
                obligations.append("RECORD_MONITOR_ONLY_DECISION")
        return PolicyDecision(
            effect=winner.effect,
            reason=winner.reason or f"{winner.policy_key}/{winner.rule_name}",
            matched=matched,
            obligations=tuple(dict.fromkeys(obligations)),
        )

    def evaluate_and_record(
        self,
        ctx: ExecutionContext,
        request: PolicyRequest,
        *,
        run_id: str | None = None,
        run_step_id: str | None = None,
    ) -> PolicyDecision:
        """Evaluate and persist the decision for audit and explainability."""
        decision = self.evaluate(ctx, request)
        self._session.execute(
            text(
                """
                INSERT INTO policy_decisions (tenant_id, run_id, run_step_id, correlation_id,
                                              subject, action, resource, effect,
                                              matched_policies, obligations, reason, enforcement)
                VALUES (:t, :run, :step, :corr, CAST(:subject AS jsonb), :action, :resource,
                        CAST(:effect AS policy_effect), CAST(:matched AS jsonb),
                        CAST(:obligations AS jsonb), :reason, :enforcement)
                """
            ),
            {
                "t": ctx.tenant_id,
                "run": run_id or (ctx.run_id or None),
                "step": run_step_id,
                "corr": ctx.correlation_id,
                "subject": json.dumps(ctx.audit_identities()),
                "action": request.action,
                "resource": request.resource,
                "effect": decision.effect,
                "matched": json.dumps(
                    [
                        {
                            "policy": m.policy_key,
                            "rule": m.rule_name,
                            "effect": m.effect,
                            "reason": m.reason,
                            "enforcement": m.enforcement,
                            "obligations": list(m.obligations),
                            "downgraded_from": m.downgraded_from,
                        }
                        for m in decision.matched
                    ],
                    default=str,
                ),
                "obligations": json.dumps(list(decision.obligations)),
                "reason": decision.reason,
                "enforcement": get_settings().policy_mode.upper(),
            },
        )
        return decision
