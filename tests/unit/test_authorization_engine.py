"""Authorization engine: every stage must be able to deny independently."""

from __future__ import annotations

import pytest

from agentic_os.core.context import AgentIdentity, ExecutionContext, HumanIdentity, ToolIdentity
from agentic_os.identity.authz import (
    AgentAuthorizationProfile,
    AuthorizationRequest,
    Resource,
    authorize,
    autonomy_rank,
    require,
)

pytestmark = pytest.mark.unit

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"


def make_ctx(
    *,
    permissions: set[str] | None = None,
    roles: set[str] | None = None,
    groups: set[str] | None = None,
    clearance: str = "INTERNAL",
    mfa: bool = False,
    agent: AgentIdentity | None = None,
    tool: ToolIdentity | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        tenant_id=TENANT,
        organization_id="org",
        human=HumanIdentity(
            user_id="u1",
            email="u1@example.test",
            roles=frozenset(roles or set()),
            permissions=frozenset(permissions or {"runs:read"}),
            groups=frozenset(groups or set()),
            mfa_satisfied=mfa,
            clearance=clearance,  # type: ignore[arg-type]
        ),
        agent=agent,
        tool=tool,
    )


def test_permission_grant_allows() -> None:
    decision = authorize(
        make_ctx(permissions={"runs:read"}),
        AuthorizationRequest(action="runs:read", resource=Resource("run", "r1", tenant_id=TENANT)),
    )
    assert decision.allowed


def test_missing_permission_denies_at_permission_stage() -> None:
    decision = authorize(
        make_ctx(permissions={"runs:read"}),
        AuthorizationRequest(action="policies:write", resource=Resource("policy", "p1")),
    )
    assert not decision.allowed
    assert decision.failed_stage == "PERMISSION"


def test_wildcard_permission_grants_resource_family() -> None:
    decision = authorize(
        make_ctx(permissions={"runs:*"}),
        AuthorizationRequest(action="runs:cancel", resource=Resource("run", "r1")),
    )
    assert decision.allowed


def test_cross_tenant_resource_denied() -> None:
    decision = authorize(
        make_ctx(permissions={"runs:read"}),
        AuthorizationRequest(
            action="runs:read", resource=Resource("run", "r1", tenant_id=OTHER_TENANT)
        ),
    )
    assert not decision.allowed
    assert decision.failed_stage == "TENANT"


def test_unauthenticated_request_denied() -> None:
    ctx = ExecutionContext(tenant_id=TENANT, organization_id="org")
    decision = authorize(
        ctx, AuthorizationRequest(action="runs:read", resource=Resource("run", "r1"))
    )
    assert not decision.allowed
    assert decision.failed_stage == "PERMISSION"


@pytest.mark.parametrize(
    ("clearance", "resource_class", "expected"),
    [
        ("INTERNAL", "PUBLIC", True),
        ("INTERNAL", "INTERNAL", True),
        ("INTERNAL", "CONFIDENTIAL", False),
        ("CONFIDENTIAL", "CONFIDENTIAL", True),
        ("CONFIDENTIAL", "RESTRICTED", False),
        ("RESTRICTED", "RESTRICTED", True),
    ],
)
def test_clearance_dominance(clearance: str, resource_class: str, expected: bool) -> None:
    decision = authorize(
        make_ctx(permissions={"knowledge:read"}, clearance=clearance),
        AuthorizationRequest(
            action="knowledge:read",
            resource=Resource("document", "d1", classification=resource_class),
        ),
    )
    assert decision.allowed is expected
    if not expected:
        assert decision.failed_stage == "CLEARANCE"


def test_acl_denies_principal_not_on_list() -> None:
    decision = authorize(
        make_ctx(permissions={"knowledge:read"}),
        AuthorizationRequest(
            action="knowledge:read",
            resource=Resource("document", "d1", acl_principals=frozenset({"USER:someone-else"})),
        ),
    )
    assert not decision.allowed
    assert decision.failed_stage == "ACL"


def test_acl_matches_group_membership() -> None:
    decision = authorize(
        make_ctx(permissions={"knowledge:read"}, groups={"systems-section"}),
        AuthorizationRequest(
            action="knowledge:read",
            resource=Resource("document", "d1", acl_principals=frozenset({"GROUP:systems-section"})),
        ),
    )
    assert decision.allowed


def test_mfa_required_denies_without_second_factor() -> None:
    decision = authorize(
        make_ctx(permissions={"approvals:decide"}, mfa=False),
        AuthorizationRequest(
            action="approvals:decide", resource=Resource("approval", "a1"), require_mfa=True
        ),
    )
    assert not decision.allowed
    assert decision.failed_stage == "MFA"


def test_mfa_satisfied_allows() -> None:
    decision = authorize(
        make_ctx(permissions={"approvals:decide"}, mfa=True),
        AuthorizationRequest(
            action="approvals:decide", resource=Resource("approval", "a1"), require_mfa=True
        ),
    )
    assert decision.allowed


# --------------------------------------------------------------------- agents
KNOWLEDGE_PROFILE = AgentAuthorizationProfile(
    agent_key="knowledge",
    max_autonomy="A1",
    allowed_tools=frozenset({"knowledge.search"}),
    allowed_skills=frozenset({"search", "summarise"}),
    allowed_models=frozenset({"general-primary"}),
    permitted_domains=frozenset({"knowledge"}),
    prohibited_domains=frozenset({"payroll"}),
    max_classification="CONFIDENTIAL",
)


def test_agent_contract_blocks_unlisted_tool() -> None:
    decision = authorize(
        make_ctx(permissions={"tools:invoke"}),
        AuthorizationRequest(action="tools:invoke", resource=Resource("tool", "finance.execute_payment")),
        agent_profile=KNOWLEDGE_PROFILE,
    )
    assert not decision.allowed
    assert decision.failed_stage == "AGENT_CONTRACT"


def test_agent_contract_blocks_unlisted_skill_and_model() -> None:
    for rtype, rid in (("skill", "reconcile"), ("model", "reasoning-primary")):
        decision = authorize(
            make_ctx(permissions={"tools:invoke"}),
            AuthorizationRequest(action="tools:invoke", resource=Resource(rtype, rid)),
            agent_profile=KNOWLEDGE_PROFILE,
        )
        assert not decision.allowed, rtype
        assert decision.failed_stage == "AGENT_CONTRACT"


def test_agent_contract_blocks_prohibited_domain() -> None:
    decision = authorize(
        make_ctx(permissions={"knowledge:read"}),
        AuthorizationRequest(
            action="knowledge:read",
            resource=Resource("document", "d1", attributes={"data_domain": "payroll"}),
        ),
        agent_profile=KNOWLEDGE_PROFILE,
    )
    assert not decision.allowed
    assert decision.failed_stage == "AGENT_CONTRACT"


def test_agent_contract_blocks_over_classification() -> None:
    decision = authorize(
        make_ctx(permissions={"knowledge:read"}, clearance="RESTRICTED"),
        AuthorizationRequest(
            action="knowledge:read",
            resource=Resource("document", "d1", classification="RESTRICTED"),
        ),
        agent_profile=KNOWLEDGE_PROFILE,
    )
    assert not decision.allowed
    assert decision.failed_stage == "AGENT_CONTRACT"


def test_autonomy_ceiling_denies_above_contract() -> None:
    decision = authorize(
        make_ctx(permissions={"tools:invoke"}),
        AuthorizationRequest(
            action="tools:invoke",
            resource=Resource("tool", "knowledge.search"),
            required_autonomy="A3",
        ),
        agent_profile=KNOWLEDGE_PROFILE,
    )
    assert not decision.allowed
    assert decision.failed_stage == "AUTONOMY"


def test_a4_escalates_to_approval_rather_than_denying() -> None:
    profile = AgentAuthorizationProfile(
        agent_key="finance",
        max_autonomy="A2",
        allowed_tools=frozenset({"finance.execute_payment"}),
    )
    decision = authorize(
        make_ctx(permissions={"tools:invoke"}),
        AuthorizationRequest(
            action="tools:invoke",
            resource=Resource("tool", "finance.execute_payment"),
            required_autonomy="A4",
            risk_class="CRITICAL",
        ),
        agent_profile=profile,
    )
    assert decision.allowed
    assert "REQUIRE_HUMAN_APPROVAL" in decision.obligations
    assert "RECORD_RISK_ASSESSMENT" in decision.obligations


def test_missing_tool_scope_denies() -> None:
    ctx = make_ctx(
        permissions={"tools:invoke"}, tool=ToolIdentity(tool_id="t", scopes=frozenset({"read"}))
    )
    decision = authorize(
        ctx,
        AuthorizationRequest(
            action="tools:invoke",
            resource=Resource("tool", "knowledge.search"),
            required_scopes=frozenset({"read", "write"}),
        ),
    )
    assert not decision.allowed
    assert decision.failed_stage == "TOOL_SCOPE"


def test_runtime_agent_identity_lowers_ceiling() -> None:
    """A runtime agent identity can only ever tighten the contract ceiling."""
    ctx = make_ctx(
        permissions={"tools:invoke"},
        agent=AgentIdentity(agent_id="operations", agent_version="3.1.0", autonomy_level="A1"),
    )
    profile = AgentAuthorizationProfile(
        agent_key="operations", max_autonomy="A3", allowed_tools=frozenset({"tasks.create"})
    )
    decision = authorize(
        ctx,
        AuthorizationRequest(
            action="tools:invoke",
            resource=Resource("tool", "tasks.create"),
            required_autonomy="A3",
        ),
        agent_profile=profile,
    )
    assert not decision.allowed
    assert decision.failed_stage == "AUTONOMY"


def test_require_raises_on_denial() -> None:
    from agentic_os.core.errors import AuthorizationError

    with pytest.raises(AuthorizationError) as excinfo:
        require(
            make_ctx(permissions={"runs:read"}),
            AuthorizationRequest(action="policies:write", resource=Resource("policy", "p1")),
        )
    assert excinfo.value.details["failed_stage"] == "PERMISSION"


def test_autonomy_rank_is_ordered() -> None:
    assert [autonomy_rank(x) for x in ("A0", "A1", "A2", "A3", "A4")] == [0, 1, 2, 3, 4]
    assert autonomy_rank("UNKNOWN") > autonomy_rank("A4")
