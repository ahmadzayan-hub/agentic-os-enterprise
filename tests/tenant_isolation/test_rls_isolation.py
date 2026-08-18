"""Cross-tenant access must fail at the database, not in application code."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

#: Every table that carries tenant data. Derived from the live catalogue so a
#: newly added table is covered automatically.
TENANT_TABLES_QUERY = """
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND a.attnum > 0
WHERE c.relkind = 'r' AND n.nspname = 'public'
ORDER BY c.relname
"""


def test_every_tenant_table_has_forced_rls(db: Session) -> None:
    rows = db.execute(
        text(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id'
            WHERE c.relkind = 'r' AND n.nspname = 'public'
            """
        )
    ).all()
    assert rows, "expected tenant-scoped tables to exist"
    unprotected = [r.relname for r in rows if not (r.relrowsecurity and r.relforcerowsecurity)]
    assert unprotected == [], f"tables without FORCE ROW LEVEL SECURITY: {unprotected}"


def test_every_tenant_table_has_an_isolation_policy(db: Session) -> None:
    tables = [r[0] for r in db.execute(text(TENANT_TABLES_QUERY)).all()]
    policied = {
        r[0]
        for r in db.execute(
            text("SELECT tablename FROM pg_policies WHERE schemaname = 'public'")
        ).all()
    }
    missing = sorted(set(tables) - policied)
    assert missing == [], f"tenant tables without an RLS policy: {missing}"


def test_application_role_cannot_bypass_rls(db: Session) -> None:
    row = db.execute(
        text("SELECT rolbypassrls, rolsuper, rolcreaterole FROM pg_roles WHERE rolname = current_user")
    ).one()
    assert row.rolbypassrls is False, "the application role must never hold BYPASSRLS"
    assert row.rolsuper is False, "the application role must never be a superuser"
    assert row.rolcreaterole is False


def test_reads_are_scoped_to_the_bound_tenant(
    db: Session, db_other: Session, tenant_id: str, other_tenant_id: str
) -> None:
    primary = {str(r[0]) for r in db.execute(text("SELECT id FROM users")).all()}
    secondary = {str(r[0]) for r in db_other.execute(text("SELECT id FROM users")).all()}
    assert primary, "primary tenant should have seeded users"
    assert secondary, "secondary tenant should have seeded users"
    assert primary.isdisjoint(secondary), "tenants must not share user rows"


def test_unbound_session_sees_nothing(db_unbound: Session) -> None:
    for table in ("users", "agents", "runs", "documents", "tools", "policies"):
        count = db_unbound.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        assert count == 0, f"unbound session saw {count} rows in {table}"


def test_cross_tenant_insert_is_rejected(
    db: Session, other_tenant_id: str, organization_id: str
) -> None:
    with pytest.raises(ProgrammingError) as excinfo:
        db.execute(
            text(
                "INSERT INTO tasks (tenant_id, title) VALUES (CAST(:t AS uuid), :title)"
            ),
            {"t": other_tenant_id, "title": "cross-tenant write attempt"},
        )
        db.flush()
    assert "row-level security" in str(excinfo.value).lower()


def test_cross_tenant_update_affects_no_rows(db: Session, db_other: Session) -> None:
    """A row created in tenant B is invisible and unmodifiable from tenant A."""
    other_task = db_other.execute(
        text(
            "INSERT INTO tasks (tenant_id, title) "
            "VALUES (app_current_tenant(), 'tenant-b task') RETURNING id"
        )
    ).scalar_one()
    db_other.flush()

    seen = db.execute(
        text("SELECT count(*) FROM tasks WHERE id = :i"), {"i": other_task}
    ).scalar_one()
    assert seen == 0

    result = db.execute(
        text("UPDATE tasks SET title = 'hijacked' WHERE id = :i"), {"i": other_task}
    )
    assert result.rowcount == 0

    result = db.execute(text("DELETE FROM tasks WHERE id = :i"), {"i": other_task})
    assert result.rowcount == 0

    db_other.rollback()


def test_tenant_row_itself_is_isolated(db: Session, other_tenant_id: str) -> None:
    visible = {str(r[0]) for r in db.execute(text("SELECT id FROM tenants")).all()}
    assert other_tenant_id not in visible
    assert len(visible) == 1


def test_setting_an_unknown_tenant_returns_nothing(db: Session) -> None:
    stranger = str(uuid.uuid4())
    db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": stranger})
    assert db.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM agents")).scalar_one() == 0


def test_malformed_tenant_binding_fails_closed(db: Session) -> None:
    """A non-uuid binding must error or return nothing, never return everything."""
    db.execute(text("SELECT set_config('app.tenant_id', 'not-a-uuid', true)"))
    try:
        count = db.execute(text("SELECT count(*) FROM users")).scalar_one()
    except DBAPIError:
        # Erroring is the correct failure mode: the cast fails and no rows are
        # returned. What must never happen is silently matching every row.
        db.rollback()
        return
    assert count == 0
