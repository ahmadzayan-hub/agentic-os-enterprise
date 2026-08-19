"""The audit ledger must be append-only and tamper-evident in the database."""

from __future__ import annotations

import pytest
from agentic_os.assurance.audit import AuditEntry, AuditLedger, redact_payload
from agentic_os.core.context import ExecutionContext, HumanIdentity
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, pytest.mark.security, requires_db]


def _ctx(tenant_id: str) -> ExecutionContext:
    return ExecutionContext(
        tenant_id=tenant_id,
        organization_id="org",
        human=HumanIdentity(user_id="00000000-0000-0000-0000-000000000001", email="t@example.test"),
    )


def test_append_links_the_chain(db: Session, tenant_id: str) -> None:
    ledger = AuditLedger(db)
    ctx = _ctx(tenant_id)
    first = ledger.append(ctx, AuditEntry(category="SECURITY", action="test.first"))
    second = ledger.append(ctx, AuditEntry(category="SECURITY", action="test.second"))
    db.flush()

    assert second["sequence_no"] == first["sequence_no"] + 1
    prev = db.execute(
        text("SELECT previous_hash FROM audit_events WHERE tenant_id = :t AND sequence_no = :s"),
        {"t": tenant_id, "s": second["sequence_no"]},
    ).scalar_one()
    assert prev == first["entry_hash"]


def test_chain_verification_reports_intact(db: Session, tenant_id: str) -> None:
    ledger = AuditLedger(db)
    ledger.append(_ctx(tenant_id), AuditEntry(category="SECURITY", action="test.verify"))
    db.flush()
    result = ledger.verify_chain(tenant_id)
    assert result["intact"] is True
    assert result["broken_at"] is None
    assert result["entries_checked"] >= 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET action = 'tampered'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ],
)
def test_ledger_rejects_mutation(db: Session, tenant_id: str, statement: str) -> None:
    AuditLedger(db).append(_ctx(tenant_id), AuditEntry(category="SECURITY", action="test.immutable"))
    db.flush()
    with pytest.raises(DBAPIError):
        db.execute(text(statement))
        db.flush()
    db.rollback()


def test_ledger_writes_are_tenant_scoped(db: Session, db_other: Session, tenant_id: str) -> None:
    AuditLedger(db).append(_ctx(tenant_id), AuditEntry(category="SECURITY", action="test.scoped"))
    db.commit()
    visible = db_other.execute(
        text("SELECT count(*) FROM audit_events WHERE action = 'test.scoped'")
    ).scalar_one()
    assert visible == 0


def test_sequence_numbers_are_gapless_per_tenant(db: Session, tenant_id: str) -> None:
    ledger = AuditLedger(db)
    ctx = _ctx(tenant_id)
    for i in range(5):
        ledger.append(ctx, AuditEntry(category="SECURITY", action=f"test.seq.{i}"))
    db.flush()
    rows = (
        db.execute(
            text("SELECT sequence_no FROM audit_events WHERE tenant_id = :t ORDER BY sequence_no"),
            {"t": tenant_id},
        )
        .scalars()
        .all()
    )
    assert rows == list(range(1, len(rows) + 1))


# ------------------------------------------------------------------ redaction
@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        {"password": "hunter2"},
        {"api_key": "sk-abcdefghijklmnopqrstuvwxyz"},
        {"nested": {"client_secret": "shhh"}},
        {"headers": {"Authorization": "Bearer abcdefghijklmnopqrstuvwxyz"}},
    ],
)
def test_sensitive_keys_are_never_written_in_clear(payload: dict) -> None:
    redacted = redact_payload(payload)
    serialised = str(redacted)
    for secret in ("hunter2", "sk-abcdefghijklmnopqrstuvwxyz", "shhh", "Bearer abcdef"):
        assert secret not in serialised


@pytest.mark.unit
def test_secret_patterns_in_free_text_are_masked() -> None:
    redacted = redact_payload({"note": "the key is sk-0123456789abcdefghij and it works"})
    assert "sk-0123456789abcdefghij" not in redacted["note"]
    assert "[REDACTED]" in redacted["note"]


@pytest.mark.unit
def test_long_strings_are_bounded() -> None:
    redacted = redact_payload({"blob": "x" * 10_000})
    assert len(redacted["blob"]) < 3_000


@pytest.mark.unit
def test_redaction_preserves_non_sensitive_structure() -> None:
    payload = {"count": 3, "ok": True, "items": ["a", "b"], "meta": {"tenant": "alpha"}}
    assert redact_payload(payload) == payload


def test_ledger_records_all_four_identities(db: Session, tenant_id: str) -> None:
    from agentic_os.core.context import AgentIdentity, ToolIdentity, WorkflowIdentity

    ctx = ExecutionContext(
        tenant_id=tenant_id,
        organization_id="org",
        human=HumanIdentity(user_id="00000000-0000-0000-0000-000000000001", email="a@b.test"),
        agent=AgentIdentity(agent_id="operations", agent_version="3.1.0", autonomy_level="A2"),
        workflow=WorkflowIdentity(workflow_id="wf", workflow_run_id="00000000-0000-0000-0000-0000000000ff"),
        tool=ToolIdentity(tool_id="knowledge.search"),
    )
    result = AuditLedger(db).append(ctx, AuditEntry(category="TOOL_CALL", action="test.identities"))
    db.flush()
    row = (
        db.execute(
            text(
                "SELECT human_id, agent_id, agent_version, workflow_run_id, tool_id "
                "FROM audit_events WHERE tenant_id = :t AND sequence_no = :s"
            ),
            {"t": tenant_id, "s": result["sequence_no"]},
        )
        .mappings()
        .one()
    )
    assert row["agent_id"] == "operations"
    assert row["agent_version"] == "3.1.0"
    assert row["tool_id"] == "knowledge.search"
    assert row["human_id"] is not None
    assert row["workflow_run_id"] is not None
