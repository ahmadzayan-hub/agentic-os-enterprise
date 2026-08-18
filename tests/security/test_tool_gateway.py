"""Tool Security Gateway: every stage must be able to deny independently."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import AgentIdentity, ExecutionContext, HumanIdentity
from agentic_os.core.errors import (
    ApprovalRequired,
    AuthorizationError,
    KillSwitchEngaged,
    NotImplementedCapability,
    PolicyDenied,
    ValidationError,
)
from agentic_os.core.ids import prefixed_id
from agentic_os.tools.gateway import ToolGateway
from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, pytest.mark.security, requires_db]


@pytest.fixture()
def operator_ctx(db: Session, tenant_id: str, organization_id: str) -> ExecutionContext:
    user = db.execute(
        text("SELECT id FROM users WHERE tenant_id = :t AND email = 'systems.lead@rta.example'"),
        {"t": tenant_id},
    ).one()
    return ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(
            user_id=str(user.id),
            email="systems.lead@rta.example",
            roles=frozenset({"operator"}),
            permissions=frozenset({"tools:invoke", "knowledge:read", "tasks:write"}),
            clearance="CONFIDENTIAL",
            mfa_satisfied=True,
        ),
    )


@pytest.fixture()
def gateway(db: Session) -> ToolGateway:
    return ToolGateway(db)


def _as_agent(ctx: ExecutionContext, agent_key: str, autonomy: str = "A3") -> ExecutionContext:
    return ctx.with_agent(
        AgentIdentity(agent_id=agent_key, agent_version="3.1.0", autonomy_level=autonomy)
    )


# ------------------------------------------------------------------ happy path
def test_permitted_read_tool_executes(gateway, operator_ctx) -> None:
    result = gateway.invoke(
        _as_agent(operator_ctx, "knowledge"),
        "knowledge.search",
        {"query": "escalator step chain", "top_k": 3},
        idempotency_key=prefixed_id("t"),
    )
    assert result.allowed
    assert result.result is not None
    assert result.policy["effect"] in ("ALLOW", "MONITOR")


def test_write_tool_verifies_its_side_effect(gateway, operator_ctx, db: Session) -> None:
    result = gateway.invoke(
        _as_agent(operator_ctx, "operations"),
        "tasks.create",
        {"title": "Inspect escalator AST-4012 step chain", "priority": "HIGH"},
        idempotency_key=prefixed_id("t"),
    )
    assert result.allowed
    assert result.verification == "VERIFIED", "a READ_BACK tool must confirm the row exists"
    exists = db.execute(
        text("SELECT 1 FROM tasks WHERE id = CAST(:i AS uuid)"),
        {"i": result.result["task_id"]},
    ).first()
    assert exists is not None


# ------------------------------------------------------------------- rejection
def test_unauthenticated_call_is_denied_at_identity(gateway, tenant_id, organization_id) -> None:
    ctx = ExecutionContext(tenant_id=tenant_id, organization_id=organization_id)
    with pytest.raises(AuthorizationError) as excinfo:
        gateway.invoke(ctx, "knowledge.search", {"query": "x"}, idempotency_key=prefixed_id("t"))
    assert excinfo.value.details["stage"] == "IDENTITY"


def test_unimplemented_tool_is_refused_not_faked(gateway, operator_ctx) -> None:
    with pytest.raises(NotImplementedCapability) as excinfo:
        gateway.invoke(
            _as_agent(operator_ctx, "finance"),
            "finance.read_invoices",
            {"limit": 10},
            idempotency_key=prefixed_id("t"),
        )
    assert excinfo.value.details["stage"] == "TOOL_RESOLUTION"
    assert excinfo.value.http_status == 501


def test_tool_outside_the_agent_contract_is_denied(gateway, operator_ctx) -> None:
    with pytest.raises(AuthorizationError) as excinfo:
        gateway.invoke(
            _as_agent(operator_ctx, "knowledge"),
            "tasks.create",
            {"title": "knowledge agent should not create tasks"},
            idempotency_key=prefixed_id("t"),
        )
    assert excinfo.value.details["stage"] == "AUTHORIZATION"


def test_conductor_holds_no_tool_authority(gateway, operator_ctx) -> None:
    """Architecture Constitution rule 17, enforced at the gateway."""
    with pytest.raises(AuthorizationError):
        gateway.invoke(
            _as_agent(operator_ctx, "conductor"),
            "knowledge.search",
            {"query": "anything"},
            idempotency_key=prefixed_id("t"),
        )


def test_invalid_parameters_are_rejected_before_execution(gateway, operator_ctx) -> None:
    with pytest.raises(ValidationError) as excinfo:
        gateway.invoke(
            _as_agent(operator_ctx, "knowledge"),
            "knowledge.search",
            {"query": "x", "top_k": 9999, "unexpected_field": "injected"},
            idempotency_key=prefixed_id("t"),
        )
    assert excinfo.value.details["stage"] == "PARAMETERS"


def test_missing_required_parameter_is_rejected(gateway, operator_ctx) -> None:
    with pytest.raises(ValidationError):
        gateway.invoke(
            _as_agent(operator_ctx, "knowledge"),
            "knowledge.search",
            {},
            idempotency_key=prefixed_id("t"),
        )


def test_consequential_tool_requires_approval(gateway, operator_ctx, db: Session) -> None:
    db.execute(
        text(
            "UPDATE tools SET implementation_status = 'IMPLEMENTED' "
            "WHERE tenant_id = :t AND tool_key = 'finance.issue_refund'"
        ),
        {"t": operator_ctx.tenant_id},
    )
    with pytest.raises((ApprovalRequired, NotImplementedCapability)) as excinfo:
        gateway.invoke(
            _as_agent(operator_ctx, "finance"),
            "finance.issue_refund",
            {"transaction_id": "INV-1188", "amount_usd": 250},
            idempotency_key=prefixed_id("t"),
        )
    db.rollback()
    # Either it is refused as unimplemented, or - if implemented - it demands
    # approval. What must never happen is silent execution.
    assert excinfo.value.error_class.value in ("APPROVAL_REQUIRED", "NOT_IMPLEMENTED")


def test_kill_switch_blocks_every_tool(gateway, operator_ctx, db: Session) -> None:
    db.execute(
        text(
            "INSERT INTO kill_switches (tenant_id, scope, target_key, engaged, reason) "
            "VALUES (:t, 'TENANT', '', true, 'test') "
            "ON CONFLICT (tenant_id, scope, target_key) WHERE tenant_id IS NOT NULL "
            "DO UPDATE SET engaged = true"
        ),
        {"t": operator_ctx.tenant_id},
    )
    try:
        with pytest.raises(KillSwitchEngaged) as excinfo:
            gateway.invoke(
                _as_agent(operator_ctx, "knowledge"),
                "knowledge.search",
                {"query": "x"},
                idempotency_key=prefixed_id("t"),
            )
        assert excinfo.value.details["stage"] == "KILL_SWITCH"
    finally:
        db.rollback()


def test_tool_scoped_kill_switch_blocks_only_that_tool(gateway, operator_ctx, db: Session) -> None:
    db.execute(
        text(
            "INSERT INTO kill_switches (tenant_id, scope, target_key, engaged) "
            "VALUES (:t, 'TOOL', 'tasks.create', true) "
            "ON CONFLICT (tenant_id, scope, target_key) WHERE tenant_id IS NOT NULL "
            "DO UPDATE SET engaged = true"
        ),
        {"t": operator_ctx.tenant_id},
    )
    try:
        with pytest.raises(KillSwitchEngaged):
            gateway.invoke(
                _as_agent(operator_ctx, "operations"), "tasks.create",
                {"title": "blocked"}, idempotency_key=prefixed_id("t"),
            )
        ok = gateway.invoke(
            _as_agent(operator_ctx, "knowledge"), "knowledge.search",
            {"query": "escalator"}, idempotency_key=prefixed_id("t"),
        )
        assert ok.allowed
    finally:
        db.rollback()


def test_over_classified_call_is_denied(gateway, operator_ctx) -> None:
    """A call carrying data above the tool's ceiling is refused."""
    from dataclasses import replace

    ctx = replace(
        _as_agent(operator_ctx, "analytics"), attributes={"classification": "RESTRICTED"}
    )
    with pytest.raises(PolicyDenied) as excinfo:
        gateway.invoke(
            ctx, "analytics.query_metrics", {"metric": "x"}, idempotency_key=prefixed_id("t")
        )
    assert excinfo.value.details["stage"] == "AUTHORIZATION"


# ---------------------------------------------------------------- idempotency
def test_repeated_call_with_the_same_key_does_not_re_execute(
    gateway, operator_ctx, db: Session
) -> None:
    key = prefixed_id("idem")
    ctx = _as_agent(operator_ctx, "operations")
    first = gateway.invoke(ctx, "tasks.create", {"title": "Idempotent task"}, idempotency_key=key)
    second = gateway.invoke(ctx, "tasks.create", {"title": "Idempotent task"}, idempotency_key=key)

    assert first.allowed
    assert second.idempotent_replay is True
    created = db.execute(
        text("SELECT count(*) FROM tasks WHERE tenant_id = :t AND title = 'Idempotent task'"),
        {"t": operator_ctx.tenant_id},
    ).scalar_one()
    assert created == 1, "an idempotent replay must not duplicate the side effect"


# ------------------------------------------------------------------- recording
def test_every_call_is_recorded_and_audited(gateway, operator_ctx, db: Session) -> None:
    key = prefixed_id("t")
    gateway.invoke(
        _as_agent(operator_ctx, "knowledge"), "knowledge.search",
        {"query": "brake pad"}, idempotency_key=key,
    )
    call = db.execute(
        text(
            "SELECT gateway_decision, parameters_hash, agent_key FROM tool_calls "
            "WHERE tenant_id = :t AND idempotency_key = :k"
        ),
        {"t": operator_ctx.tenant_id, "k": key},
    ).mappings().one()
    assert call["gateway_decision"] == "ALLOWED"
    assert call["parameters_hash"]
    assert call["agent_key"] == "knowledge"

    audited = db.execute(
        text(
            "SELECT count(*) FROM audit_events WHERE tenant_id = :t AND category = 'TOOL_CALL'"
        ),
        {"t": operator_ctx.tenant_id},
    ).scalar_one()
    assert audited >= 1


def test_denied_calls_are_recorded_with_their_stage(gateway, operator_ctx, db: Session) -> None:
    key = prefixed_id("t")
    with pytest.raises(NotImplementedCapability):
        gateway.invoke(
            _as_agent(operator_ctx, "finance"), "finance.read_invoices",
            {"limit": 5}, idempotency_key=key,
        )
    row = db.execute(
        text(
            "SELECT gateway_decision, denial_stage FROM tool_calls "
            "WHERE tenant_id = :t AND idempotency_key = :k"
        ),
        {"t": operator_ctx.tenant_id, "k": key},
    ).mappings().first()
    assert row is not None, "a denial must still be recorded"
    assert row["gateway_decision"] == "DENIED"
    assert row["denial_stage"] == "TOOL_RESOLUTION"


def test_secrets_in_parameters_are_never_persisted(gateway, operator_ctx, db: Session) -> None:
    key = prefixed_id("t")
    gateway.invoke(
        _as_agent(operator_ctx, "operations"),
        "tasks.create",
        {"title": "Rotate credential", "description": "api_key: sk-abcdefghijklmnop0123456789"},
        idempotency_key=key,
    )
    stored = db.execute(
        text(
            "SELECT parameters_redacted::text AS p, result_redacted::text AS r FROM tool_calls "
            "WHERE tenant_id = :t AND idempotency_key = :k"
        ),
        {"t": operator_ctx.tenant_id, "k": key},
    ).mappings().one()
    assert "sk-abcdefghijklmnop0123456789" not in stored["p"]
    assert "sk-abcdefghijklmnop0123456789" not in (stored["r"] or "")
