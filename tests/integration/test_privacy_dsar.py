"""Data subject request processing against a real database.

These tests exercise the privacy plane end to end: a request is recorded,
executed, and its effect on every table holding subject data is measured. The
two behaviours that matter for the control are that an active legal hold stops
an erasure outright, and that erasure never removes the audit ledger.
"""

from __future__ import annotations

from typing import Any

import pytest
from agentic_os.assurance.audit import AuditEntry, AuditLedger
from agentic_os.privacy import dsar
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

SUBJECT_EMAIL = "dsar.subject@rta.example"


def _make_subject(db: Session, tenant_id: str, organization_id: str) -> dict[str, Any]:
    """A throwaway subject with data spread across the tables DSAR touches."""
    user_id = db.execute(
        text(
            """
            INSERT INTO users (tenant_id, organization_id, email, display_name,
                               password_hash, clearance, status)
            VALUES (:t, :o, :e, 'DSAR Subject', 'argon2-placeholder', 'INTERNAL', 'ACTIVE')
            RETURNING id
            """
        ),
        {"t": tenant_id, "o": organization_id, "e": SUBJECT_EMAIL},
    ).scalar_one()

    db.execute(
        text(
            """
            INSERT INTO sessions (tenant_id, user_id, refresh_token_hash, expires_at)
            VALUES (:t, :u, :h, now() + interval '1 day')
            """
        ),
        {"t": tenant_id, "u": user_id, "h": f"hash-{user_id}"},
    )
    document_id = db.execute(
        text(
            """
            INSERT INTO documents (tenant_id, organization_id, title, content_hash,
                                   owner_user_id, classification)
            VALUES (:t, :o, 'Subject owned note', :h, :u, 'INTERNAL')
            RETURNING id
            """
        ),
        {"t": tenant_id, "o": organization_id, "h": f"doc-{user_id}", "u": user_id},
    ).scalar_one()
    db.execute(
        text(
            """
            INSERT INTO memory_records (tenant_id, memory_type, subject_type, subject_id,
                                        content, content_hash, owner_user_id)
            VALUES (:t, 'EPISODIC', 'USER', :s, 'Subject preference', :h, :u)
            """
        ),
        {"t": tenant_id, "s": str(user_id), "h": f"mem-{user_id}", "u": user_id},
    )
    return {"user_id": str(user_id), "document_id": str(document_id)}


@pytest.fixture()
def subject(db: Session, tenant_id: str, organization_id: str) -> dict[str, Any]:
    return _make_subject(db, tenant_id, organization_id)


def _hold(db: Session, tenant_id: str, key: str) -> None:
    db.execute(
        text(
            """
            INSERT INTO legal_holds (tenant_id, hold_key, reason, resource_type)
            VALUES (:t, :k, 'Arbitration over the Green Line maintenance claim', 'user')
            """
        ),
        {"t": tenant_id, "k": key},
    )


def _clear_holds(db: Session, tenant_id: str) -> None:
    db.execute(
        text("UPDATE legal_holds SET active = false, released_at = now() WHERE tenant_id = :t"),
        {"t": tenant_id},
    )


def test_access_request_collects_the_subjects_records(db, ctx, subject):
    _clear_holds(db, ctx.tenant_id)
    request_id = dsar.raise_request(db, ctx, request_type="ACCESS", subject_email=SUBJECT_EMAIL)
    result = dsar.process(db, ctx, request_id)

    assert result.status == "COMPLETED"
    assert result.export is not None
    records = result.export["records"]
    assert records["users"][0]["email"] == SUBJECT_EMAIL
    assert len(records["sessions"]) == 1
    assert len(records["documents"]) == 1
    assert len(records["memory_records"]) == 1

    status = db.execute(
        text("SELECT status FROM data_subject_requests WHERE id = CAST(:i AS uuid)"),
        {"i": request_id},
    ).scalar_one()
    assert status == "COMPLETED"


def test_export_never_discloses_credential_material(db, ctx, subject):
    _clear_holds(db, ctx.tenant_id)
    request_id = dsar.raise_request(db, ctx, request_type="EXPORT", subject_email=SUBJECT_EMAIL)
    result = dsar.process(db, ctx, request_id)

    exported = result.export["records"]
    assert "password_hash" not in exported["users"][0]
    assert "refresh_token_hash" not in exported["sessions"][0]


def test_erasure_is_blocked_by_an_active_legal_hold(db, ctx, subject):
    """The control: a hold beats a deletion request, and nothing is erased."""
    _clear_holds(db, ctx.tenant_id)
    _hold(db, ctx.tenant_id, "hold-green-line-arbitration")

    request_id = dsar.raise_request(db, ctx, request_type="DELETE", subject_email=SUBJECT_EMAIL)
    result = dsar.process(db, ctx, request_id)

    assert result.status == "BLOCKED_BY_HOLD"
    assert "hold-green-line-arbitration" in result.blocked_by
    assert result.affected == {}

    # Nothing was partially executed.
    still_there = (
        db.execute(
            text("SELECT email, status FROM users WHERE tenant_id = :t AND id = CAST(:u AS uuid)"),
            {"t": ctx.tenant_id, "u": subject["user_id"]},
        )
        .mappings()
        .one()
    )
    assert still_there["email"] == SUBJECT_EMAIL
    assert still_there["status"] == "ACTIVE"
    assert (
        db.execute(
            text("SELECT count(*) FROM sessions WHERE user_id = CAST(:u AS uuid)"),
            {"u": subject["user_id"]},
        ).scalar_one()
        == 1
    )

    stored = (
        db.execute(
            text("SELECT status, affected_records FROM data_subject_requests WHERE id = CAST(:i AS uuid)"),
            {"i": request_id},
        )
        .mappings()
        .one()
    )
    assert stored["status"] == "BLOCKED_BY_HOLD"
    assert stored["affected_records"]["blocked_by"] == ["hold-green-line-arbitration"]


def test_erasure_detaches_records_and_pseudonymises_the_account(db, ctx, subject):
    _clear_holds(db, ctx.tenant_id)
    request_id = dsar.raise_request(db, ctx, request_type="DELETE", subject_email=SUBJECT_EMAIL)
    result = dsar.process(db, ctx, request_id)

    assert result.status == "COMPLETED"
    assert result.affected["sessions"] == 1
    assert result.affected["documents"] == 1
    assert result.affected["memory_records"] == 1

    user = (
        db.execute(
            text(
                "SELECT email, display_name, password_hash, status, deleted_at "
                "FROM users WHERE tenant_id = :t AND id = CAST(:u AS uuid)"
            ),
            {"t": ctx.tenant_id, "u": subject["user_id"]},
        )
        .mappings()
        .one()
    )
    assert user["email"].startswith("erased+")
    assert user["email"].endswith("@invalid.local")
    assert user["password_hash"] is None
    assert user["status"] == "RETIRED"
    assert user["deleted_at"] is not None

    # The document survives, detached from the person.
    document = (
        db.execute(
            text("SELECT title, owner_user_id FROM documents WHERE id = CAST(:d AS uuid)"),
            {"d": subject["document_id"]},
        )
        .mappings()
        .one()
    )
    assert document["title"] == "Subject owned note"
    assert document["owner_user_id"] is None

    # Credential material is gone outright.
    assert (
        db.execute(
            text("SELECT count(*) FROM sessions WHERE user_id = CAST(:u AS uuid)"),
            {"u": subject["user_id"]},
        ).scalar_one()
        == 0
    )


def test_erasure_retains_the_append_only_audit_ledger(db, ctx, subject):
    """Erasure must not be able to launder the record that something happened."""
    _clear_holds(db, ctx.tenant_id)
    AuditLedger(db).append(
        ctx,
        AuditEntry(
            category="USER_ACTION",
            action="test.subject_activity",
            resource_type="user",
            resource_id=subject["user_id"],
        ),
    )
    before = db.execute(
        text("SELECT count(*) FROM audit_events WHERE tenant_id = :t"), {"t": ctx.tenant_id}
    ).scalar_one()

    request_id = dsar.raise_request(db, ctx, request_type="DELETE", subject_email=SUBJECT_EMAIL)
    result = dsar.process(db, ctx, request_id)

    after = db.execute(
        text("SELECT count(*) FROM audit_events WHERE tenant_id = :t"), {"t": ctx.tenant_id}
    ).scalar_one()
    assert after > before, "erasure must only ever add to the ledger"
    assert "audit_events" in result.retained
    assert "audit_events" not in result.affected


def test_a_request_is_not_processed_twice(db, ctx, subject):
    from agentic_os.core.errors import Conflict

    _clear_holds(db, ctx.tenant_id)
    request_id = dsar.raise_request(db, ctx, request_type="ACCESS", subject_email=SUBJECT_EMAIL)
    dsar.process(db, ctx, request_id)
    with pytest.raises(Conflict):
        dsar.process(db, ctx, request_id)


def test_retention_expires_documents_but_stops_at_a_legal_hold(db, ctx, tenant_id, organization_id):
    _clear_holds(db, ctx.tenant_id)
    db.execute(
        text(
            """
            INSERT INTO documents (tenant_id, organization_id, title, content_hash,
                                   retention_until)
            VALUES (:t, :o, 'Expired maintenance log', :h, now() - interval '1 day')
            """
        ),
        {"t": tenant_id, "o": organization_id, "h": "doc-retention-expired"},
    )

    removed = dsar.apply_retention(db, ctx)
    assert removed.get("documents", 0) >= 1

    _hold(db, tenant_id, "hold-retention-freeze")
    frozen = dsar.apply_retention(db, ctx)
    assert frozen == {"skipped_due_to_legal_hold": 1}
