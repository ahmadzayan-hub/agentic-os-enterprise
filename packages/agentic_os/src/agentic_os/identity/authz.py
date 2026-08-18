"""Authorization engine combining RBAC, ABAC, classification and autonomy.

A decision is the conjunction of independent checks — every one must pass, and
the first failure is reported with the stage that produced it so denials are
diagnosable. The engine is pure: it takes a request and returns a decision, and
performs no I/O, which makes it exhaustively testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agentic_os.core.context import ExecutionContext, classification_rank

AUTONOMY_ORDER = ("A0", "A1", "A2", "A3", "A4")


def autonomy_rank(level: str) -> int:
    try:
        return AUTONOMY_ORDER.index(level)
    except ValueError:
        return len(AUTONOMY_ORDER)


@dataclass(frozen=True, slots=True)
class Resource:
    resource_type: str
    resource_id: str = ""
    tenant_id: str = ""
    classification: str = "INTERNAL"
    owner_user_id: str = ""
    owner_team: str = ""
    acl_principals: frozenset[str] = field(default_factory=frozenset)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    action: str
    resource: Resource
    required_autonomy: str = "A0"
    required_scopes: frozenset[str] = field(default_factory=frozenset)
    risk_class: str = "LOW"
    require_mfa: bool = False


Stage = Literal[
    "TENANT",
    "PERMISSION",
    "CLEARANCE",
    "ACL",
    "AGENT_CONTRACT",
    "AUTONOMY",
    "TOOL_SCOPE",
    "MFA",
    "RISK",
]


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: str = ""
    failed_stage: Stage | None = None
    obligations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "failed_stage": self.failed_stage,
            "obligations": list(self.obligations),
        }


ALLOWED = AuthorizationDecision(allowed=True, reason="authorized")


def _deny(stage: Stage, reason: str) -> AuthorizationDecision:
    return AuthorizationDecision(allowed=False, reason=reason, failed_stage=stage)


@dataclass(frozen=True, slots=True)
class AgentAuthorizationProfile:
    """The subset of an agent contract the authorization engine needs."""

    agent_key: str
    max_autonomy: str = "A1"
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    allowed_skills: frozenset[str] = field(default_factory=frozenset)
    allowed_models: frozenset[str] = field(default_factory=frozenset)
    permitted_domains: frozenset[str] = field(default_factory=frozenset)
    prohibited_domains: frozenset[str] = field(default_factory=frozenset)
    max_classification: str = "INTERNAL"


def authorize(
    ctx: ExecutionContext,
    request: AuthorizationRequest,
    *,
    agent_profile: AgentAuthorizationProfile | None = None,
) -> AuthorizationDecision:
    """Evaluate one authorization request. Fails closed at the first problem."""
    obligations: list[str] = []

    # --- tenant -----------------------------------------------------------
    if request.resource.tenant_id and request.resource.tenant_id != ctx.tenant_id:
        return _deny(
            "TENANT",
            f"resource belongs to tenant {request.resource.tenant_id}, "
            f"caller is bound to {ctx.tenant_id}",
        )

    # --- RBAC -------------------------------------------------------------
    if ctx.human is None and ctx.service_principal == "":
        return _deny("PERMISSION", "no authenticated principal on the request")
    if ctx.human is not None and not ctx.has_permission(request.action):
        return _deny("PERMISSION", f"principal lacks permission '{request.action}'")

    # --- MFA --------------------------------------------------------------
    if request.require_mfa and (ctx.human is None or not ctx.human.mfa_satisfied):
        return _deny("MFA", "multi-factor authentication is required for this action")

    # --- ABAC: data classification ---------------------------------------
    if ctx.human is not None:
        if classification_rank(request.resource.classification) > classification_rank(
            ctx.human.clearance
        ):
            return _deny(
                "CLEARANCE",
                f"resource classification {request.resource.classification} exceeds "
                f"principal clearance {ctx.human.clearance}",
            )

    # --- ABAC: resource ACL ----------------------------------------------
    if request.resource.acl_principals:
        principals = _principal_keys(ctx)
        if not (principals & request.resource.acl_principals):
            return _deny(
                "ACL",
                "principal is not on the resource access control list",
            )

    # --- agent contract ---------------------------------------------------
    if agent_profile is not None:
        decision = _check_agent_contract(request, agent_profile)
        if not decision.allowed:
            return decision
        obligations.extend(decision.obligations)

    # --- autonomy ---------------------------------------------------------
    effective_ceiling = _autonomy_ceiling(ctx, agent_profile)
    if autonomy_rank(request.required_autonomy) > autonomy_rank(effective_ceiling):
        # A4 work is not denied outright — it is escalated to human approval.
        if request.required_autonomy == "A4":
            obligations.append("REQUIRE_HUMAN_APPROVAL")
        else:
            return _deny(
                "AUTONOMY",
                f"action requires autonomy {request.required_autonomy} but the "
                f"effective ceiling is {effective_ceiling}",
            )

    # --- tool scopes ------------------------------------------------------
    if request.required_scopes:
        granted = ctx.tool.scopes if ctx.tool else frozenset()
        missing = request.required_scopes - granted
        if missing:
            return _deny("TOOL_SCOPE", f"missing tool scopes: {sorted(missing)}")

    # --- risk -------------------------------------------------------------
    if request.risk_class in ("HIGH", "CRITICAL"):
        obligations.append("RECORD_RISK_ASSESSMENT")
    if request.risk_class == "CRITICAL" and "REQUIRE_HUMAN_APPROVAL" not in obligations:
        obligations.append("REQUIRE_HUMAN_APPROVAL")

    return AuthorizationDecision(
        allowed=True, reason="authorized", obligations=tuple(dict.fromkeys(obligations))
    )


def _principal_keys(ctx: ExecutionContext) -> frozenset[str]:
    """All ACL principal identifiers this context can match."""
    keys = {"PUBLIC"}
    if ctx.human is not None:
        keys.add(f"USER:{ctx.human.user_id}")
        keys.update(f"GROUP:{g}" for g in ctx.human.groups)
        keys.update(f"ROLE:{r}" for r in ctx.human.roles)
    if ctx.agent is not None:
        keys.add(f"AGENT:{ctx.agent.agent_id}")
    return frozenset(keys)


def _autonomy_ceiling(
    ctx: ExecutionContext, agent_profile: AgentAuthorizationProfile | None
) -> str:
    """Lowest of the agent contract ceiling and the runtime agent identity."""
    candidates = []
    if agent_profile is not None:
        candidates.append(agent_profile.max_autonomy)
    if ctx.agent is not None:
        candidates.append(ctx.agent.autonomy_level)
    if not candidates:
        # A human acting directly is bounded by A3; consequential work still
        # routes through the approval engine.
        return "A3"
    return min(candidates, key=autonomy_rank)


def _check_agent_contract(
    request: AuthorizationRequest, profile: AgentAuthorizationProfile
) -> AuthorizationDecision:
    resource = request.resource
    rtype, rid = resource.resource_type, resource.resource_id

    if rtype == "tool" and rid and rid not in profile.allowed_tools:
        return _deny(
            "AGENT_CONTRACT",
            f"agent '{profile.agent_key}' contract does not permit tool '{rid}'",
        )
    if rtype == "skill" and rid and rid not in profile.allowed_skills:
        return _deny(
            "AGENT_CONTRACT",
            f"agent '{profile.agent_key}' contract does not permit skill '{rid}'",
        )
    if rtype == "model" and rid and rid not in profile.allowed_models:
        return _deny(
            "AGENT_CONTRACT",
            f"agent '{profile.agent_key}' contract does not permit model '{rid}'",
        )

    domain = str(resource.attributes.get("data_domain", "")) or resource.owner_team
    if domain:
        if domain in profile.prohibited_domains:
            return _deny(
                "AGENT_CONTRACT",
                f"agent '{profile.agent_key}' is prohibited from data domain '{domain}'",
            )
        if profile.permitted_domains and domain not in profile.permitted_domains:
            return _deny(
                "AGENT_CONTRACT",
                f"agent '{profile.agent_key}' is not permitted data domain '{domain}'",
            )

    if classification_rank(resource.classification) > classification_rank(
        profile.max_classification
    ):
        return _deny(
            "AGENT_CONTRACT",
            f"agent '{profile.agent_key}' may not handle {resource.classification} data "
            f"(contract ceiling {profile.max_classification})",
        )

    return ALLOWED


def require(
    ctx: ExecutionContext,
    request: AuthorizationRequest,
    *,
    agent_profile: AgentAuthorizationProfile | None = None,
) -> AuthorizationDecision:
    """Authorize or raise :class:`AuthorizationError`."""
    from agentic_os.core.errors import AuthorizationError

    decision = authorize(ctx, request, agent_profile=agent_profile)
    if not decision.allowed:
        raise AuthorizationError(
            decision.reason,
            details={"failed_stage": decision.failed_stage, "action": request.action},
            correlation_id=ctx.correlation_id,
        )
    return decision
