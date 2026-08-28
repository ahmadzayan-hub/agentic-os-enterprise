"""Retiring a tenant.

This is the procedure the readiness report listed as a *risk* rather than a
feature: a decision with history cannot be deleted, and neither can its tenant,
so offboarding has to be something other than a DELETE. These tests pin what
that something is — and, just as importantly, what it deliberately leaves
behind.
"""

from __future__ import annotations

import uuid

import pytest
from agentic_os.core.context import ExecutionContext, HumanIdentity
from agentic_os.core.errors import AuthorizationError, Conflict
from agentic_os.privacy.offboarding import RETAINED, retire_tenant
from sqlalchemy import text

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


def _ctx(tenant_id: str, organization_id: str, user_id: str, **kw) -> ExecutionContext:
    return ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(
            user_id=user_id,
            email="offboard@rta.example",
            permissions=kw.pop("permissions", frozenset({"*"})),
            mfa_satisfied=kw.pop("mfa", True),
        ),
    )


@pytest.fixture()
def scratch_tenant(provisioning_db, seeded):
    """A tenant of its own, so retirement never touches the seeded ones."""
    org_id = provisioning_db.execute(text("SELECT id FROM organizations LIMIT 1")).scalar_one()
    slug = f"retire-{uuid.uuid4().hex[:8]}"
    tenant_id = str(
        provisioning_db.execute(
            text(
                "INSERT INTO tenants (organization_id, slug, name) "
                "VALUES (:o, :s, 'Retirement scratch') RETURNING id"
            ),
            {"o": org_id, "s": slug},
        ).scalar_one()
    )
    user_id = str(
        provisioning_db.execute(
            text(
                "INSERT INTO users (tenant_id, organization_id, email, display_name, "
                "password_hash) VALUES (CAST(:t AS uuid), :o, :e, 'Scratch user', 'x') "
                "RETURNING id"
            ),
            {"t": tenant_id, "o": org_id, "e": f"{slug}@rta.example"},
        ).scalar_one()
    )
    provisioning_db.commit()
    return {"tenant_id": tenant_id, "organization_id": str(org_id), "user_id": user_id}


@pytest.fixture()
def bound(scratch_tenant):
    """An application session bound to the scratch tenant."""
    from agentic_os.core.db import bind_tenant, get_session_factory

    session = get_session_factory()()
    bind_tenant(session, scratch_tenant["tenant_id"], actor="test")
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _ctx_for(scratch_tenant, **kw) -> ExecutionContext:
    return _ctx(
        scratch_tenant["tenant_id"],
        scratch_tenant["organization_id"],
        scratch_tenant["user_id"],
        **kw,
    )


# ------------------------------------------------------------------ refusals
def test_retirement_requires_a_second_factor(bound, scratch_tenant) -> None:
    ctx = _ctx_for(scratch_tenant, mfa=False)
    with pytest.raises(AuthorizationError) as excinfo:
        retire_tenant(bound, ctx, tenant_id=scratch_tenant["tenant_id"], reason="contract ended")
    assert "second factor" in str(excinfo.value)


def test_retirement_requires_the_write_permission(bound, scratch_tenant) -> None:
    ctx = _ctx_for(scratch_tenant, permissions=frozenset({"org:read"}))
    with pytest.raises(AuthorizationError) as excinfo:
        retire_tenant(bound, ctx, tenant_id=scratch_tenant["tenant_id"], reason="contract ended")
    assert "org:write" in str(excinfo.value)


def test_retirement_requires_a_stated_reason(bound, scratch_tenant) -> None:
    """An offboarding nobody explained is one nobody can review."""
    with pytest.raises(Conflict):
        retire_tenant(bound, _ctx_for(scratch_tenant), tenant_id=scratch_tenant["tenant_id"], reason="   ")


def test_a_tenant_cannot_be_retired_from_another_tenants_session(bound, scratch_tenant) -> None:
    """The one thing the isolation model exists to prevent."""
    other = str(uuid.uuid4())
    with pytest.raises(AuthorizationError) as excinfo:
        retire_tenant(bound, _ctx_for(scratch_tenant), tenant_id=other, reason="contract ended")
    assert "cross-tenant" in str(excinfo.value)


def test_a_legal_hold_outranks_the_retirement(bound, scratch_tenant) -> None:
    """A hold is a legal instruction; a retirement is a commercial decision."""
    bound.execute(
        text(
            "INSERT INTO legal_holds (tenant_id, hold_key, reason, resource_type, active) "
            "VALUES (CAST(:t AS uuid), :k, 'litigation', 'tenant', true)"
        ),
        {"t": scratch_tenant["tenant_id"], "k": f"hold-{uuid.uuid4().hex[:8]}"},
    )
    bound.flush()

    result = retire_tenant(
        bound, _ctx_for(scratch_tenant), tenant_id=scratch_tenant["tenant_id"], reason="contract ended"
    )
    assert result.status == "BLOCKED_BY_HOLD"
    assert result.blocked_by

    still_active = bound.execute(
        text("SELECT status FROM tenants WHERE id = CAST(:t AS uuid)"),
        {"t": scratch_tenant["tenant_id"]},
    ).scalar_one()
    assert str(still_active) == "ACTIVE", "a held tenant must not be retired"


# ---------------------------------------------------------------- retirement
def test_retirement_revokes_access_and_pseudonymises_users(bound, scratch_tenant) -> None:
    bound.execute(
        text(
            "INSERT INTO sessions (tenant_id, user_id, refresh_token_hash, expires_at) "
            "VALUES (CAST(:t AS uuid), CAST(:u AS uuid), 'h', now() + interval '1 day')"
        ),
        {"t": scratch_tenant["tenant_id"], "u": scratch_tenant["user_id"]},
    )
    bound.flush()

    result = retire_tenant(
        bound, _ctx_for(scratch_tenant), tenant_id=scratch_tenant["tenant_id"], reason="contract ended"
    )
    bound.flush()

    assert result.status == "RETIRED"
    assert result.sessions_revoked >= 1
    assert result.users_pseudonymised >= 1

    email = bound.execute(
        text("SELECT email FROM users WHERE id = CAST(:u AS uuid)"),
        {"u": scratch_tenant["user_id"]},
    ).scalar_one()
    assert str(email).startswith("retired+")
    assert str(email).endswith("@invalid"), "a pseudonym must not be a routable address"

    status = bound.execute(
        text("SELECT status FROM tenants WHERE id = CAST(:t AS uuid)"),
        {"t": scratch_tenant["tenant_id"]},
    ).scalar_one()
    assert str(status) == "RETIRED"


def test_retirement_is_recorded_in_the_ledger(bound, scratch_tenant) -> None:
    retire_tenant(
        bound, _ctx_for(scratch_tenant), tenant_id=scratch_tenant["tenant_id"], reason="contract ended"
    )
    bound.flush()
    action = bound.execute(
        text(
            "SELECT action FROM audit_events WHERE tenant_id = CAST(:t AS uuid) "
            "AND resource_id = :r ORDER BY sequence_no DESC LIMIT 1"
        ),
        {"t": scratch_tenant["tenant_id"], "r": scratch_tenant["tenant_id"]},
    ).scalar_one()
    assert action == "tenant.retired"


def test_retiring_twice_is_refused(bound, scratch_tenant) -> None:
    ctx = _ctx_for(scratch_tenant)
    retire_tenant(bound, ctx, tenant_id=scratch_tenant["tenant_id"], reason="contract ended")
    bound.flush()
    with pytest.raises(Conflict):
        retire_tenant(bound, ctx, tenant_id=scratch_tenant["tenant_id"], reason="again")


# ------------------------------------------------------- what is left behind
def test_the_caller_is_told_what_is_retained(bound, scratch_tenant) -> None:
    """An operator closing a tenant learns what still exists now, rather than
    discovering it during an audit two years later."""
    result = retire_tenant(
        bound, _ctx_for(scratch_tenant), tenant_id=scratch_tenant["tenant_id"], reason="contract ended"
    )
    assert "audit_events" in result.retained
    assert "decision_transitions" in result.retained
    assert "not erased" in result.to_dict()["note"]
    for reason in result.retained.values():
        assert reason, "every retained table must state why"


def test_the_ledger_survives_retirement(bound, scratch_tenant) -> None:
    """The point of the whole design: the evidence outlives the relationship."""
    retire_tenant(
        bound, _ctx_for(scratch_tenant), tenant_id=scratch_tenant["tenant_id"], reason="contract ended"
    )
    bound.flush()
    entries = bound.execute(
        text("SELECT count(*) FROM audit_events WHERE tenant_id = CAST(:t AS uuid)"),
        {"t": scratch_tenant["tenant_id"]},
    ).scalar_one()
    assert entries >= 1


def test_every_retained_table_is_one_that_genuinely_cannot_be_deleted(provisioning_db, seeded) -> None:
    """A guard on the explanation, not just the behaviour.

    If a table appears in RETAINED that has no append-only trigger and no
    stated reason to survive, the list has become a place to park inconvenient
    data rather than a statement about integrity.
    """
    protected = {
        str(r)
        for r in provisioning_db.execute(
            text(
                "SELECT DISTINCT c.relname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE NOT t.tgisinternal"
            )
        ).scalars()
    }
    unexplained = [table for table in ("audit_events", "decision_transitions") if table not in protected]
    assert unexplained == [], f"listed as retained but carries no trigger: {unexplained}"
    assert set(RETAINED) >= {"audit_events", "decision_transitions"}
