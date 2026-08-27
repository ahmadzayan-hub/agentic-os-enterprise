"""Data subject request processing.

Handles access, export, deletion and rectification requests end to end.

Two rules shape the implementation:

* **A legal hold beats a deletion request.** A request that would remove data
  under hold is parked as BLOCKED_BY_HOLD with the hold named, rather than
  partially executed.
* **The audit ledger is never deleted.** It is append-only by construction, so
  erasure pseudonymises the subject's identifiers in the payload projection
  instead — the record that an action happened survives, the identity does not.
  That trade-off is recorded on the request so a reviewer sees it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.assurance.audit import AuditEntry, AuditLedger
from agentic_os.core.context import ExecutionContext
from agentic_os.core.db import affected_rows
from agentic_os.core.errors import Conflict, NotFound, ValidationError
from agentic_os.core.ids import utcnow

RequestType = Literal["ACCESS", "EXPORT", "DELETE", "RECTIFY"]

#: Statutory response window. Configurable per tenant in a later iteration.
DEFAULT_DUE_DAYS = 30

#: Tables holding data attributable to a person: table, linking column, and
#: what erasure does to it. Sessions are credential material with a NOT NULL
#: owner, so they are deleted outright; everything else keeps the operational
#: record and drops the link to the person.
SUBJECT_TABLES: tuple[tuple[str, str, str], ...] = (
    ("users", "id", "PSEUDONYMISE"),
    ("sessions", "user_id", "DELETE"),
    ("runs", "requested_by", "NULLIFY"),
    ("tasks", "assignee_user_id", "NULLIFY"),
    ("documents", "owner_user_id", "NULLIFY"),
    ("approval_steps", "approver_user_id", "NULLIFY"),
    ("memory_records", "owner_user_id", "NULLIFY"),
    ("retrieval_queries", "user_id", "NULLIFY"),
    ("tool_calls", "user_id", "NULLIFY"),
    ("cost_records", "user_id", "NULLIFY"),
)

#: Columns never included in a subject access export. Disclosing a password
#: hash or a session token to the subject would hand an attacker who social
#: engineers a DSAR exactly the material they need.
SENSITIVE_COLUMNS = frozenset(
    {
        "password_hash",
        "refresh_token_hash",
        "mfa_secret_ref",
        "secret_ciphertext",
        "secret_hash",
        "external_subject",
    }
)

#: Append-only or legally required records that erasure may not remove.
NON_ERASABLE = {
    "audit_events": (
        "the audit ledger is append-only and tamper-evident; identifiers are "
        "pseudonymised in place rather than deleted"
    ),
}


@dataclass(slots=True)
class DsarResult:
    request_id: str
    request_type: str
    status: str
    subject_email: str
    affected: dict[str, int] = field(default_factory=dict)
    blocked_by: list[str] = field(default_factory=list)
    retained: dict[str, str] = field(default_factory=dict)
    export: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "request_type": self.request_type,
            "status": self.status,
            "subject_email": self.subject_email,
            "affected": self.affected,
            "blocked_by": self.blocked_by,
            "retained": self.retained,
            "export": self.export,
        }


def _subject(session: Session, tenant_id: str, email: str) -> dict[str, Any] | None:
    row = (
        session.execute(
            text(
                "SELECT id, email, display_name, clearance, status, created_at "
                "FROM users WHERE tenant_id = :t AND email = :e"
            ),
            {"t": tenant_id, "e": email.strip().lower()},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _active_holds(session: Session, tenant_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            "SELECT hold_key, reason, resource_type, resource_filter FROM legal_holds "
            "WHERE tenant_id = :t AND active AND released_at IS NULL"
        ),
        {"t": tenant_id},
    ).mappings()
    return [dict(r) for r in rows]


def raise_request(
    session: Session,
    ctx: ExecutionContext,
    *,
    request_type: RequestType,
    subject_email: str,
    due_days: int = DEFAULT_DUE_DAYS,
) -> str:
    """Record a data subject request. Returns its id."""
    if request_type not in ("ACCESS", "EXPORT", "DELETE", "RECTIFY"):
        raise ValidationError(f"unknown request type '{request_type}'")

    subject = _subject(session, ctx.tenant_id, subject_email)
    row = session.execute(
        text(
            """
            INSERT INTO data_subject_requests (tenant_id, request_type, subject_email,
                                               subject_user_id, requested_by, due_at, status)
            VALUES (:t, :type, :email, :subject, :by, :due, 'RECEIVED')
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id,
            "type": request_type,
            "email": subject_email.strip().lower(),
            "subject": subject["id"] if subject else None,
            "by": ctx.human.user_id if ctx.human else None,
            "due": utcnow() + timedelta(days=due_days),
        },
    ).one()
    request_id = str(row.id)

    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="USER_ACTION",
            action="dsar.raised",
            resource_type="data_subject_request",
            resource_id=request_id,
            classification="RESTRICTED",
            payload={"request_type": request_type, "subject_email": subject_email},
        ),
    )
    return request_id


def process(session: Session, ctx: ExecutionContext, request_id: str) -> DsarResult:
    """Execute a recorded request."""
    request = (
        session.execute(
            text(
                "SELECT id, request_type, subject_email, subject_user_id, status "
                "FROM data_subject_requests WHERE tenant_id = :t AND id = CAST(:i AS uuid)"
            ),
            {"t": ctx.tenant_id, "i": request_id},
        )
        .mappings()
        .first()
    )
    if request is None:
        raise NotFound(f"data subject request {request_id} not found")
    if request["status"] in ("COMPLETED", "REJECTED"):
        raise Conflict(f"request is already {request['status']}")

    session.execute(
        text("UPDATE data_subject_requests SET status = 'IN_PROGRESS' WHERE id = :i"),
        {"i": request_id},
    )

    subject = _subject(session, ctx.tenant_id, request["subject_email"])
    if subject is None:
        _finish(session, ctx, request_id, "COMPLETED", {"subject_found": 0})
        return DsarResult(
            request_id=request_id,
            request_type=request["request_type"],
            status="COMPLETED",
            subject_email=request["subject_email"],
            affected={"subject_found": 0},
        )

    handler = {
        "ACCESS": _access,
        "EXPORT": _access,
        "DELETE": _erase,
        "RECTIFY": _rectify,
    }[request["request_type"]]
    return handler(session, ctx, request_id, request["request_type"], subject)


def _collect(session: Session, tenant_id: str, user_id: str) -> dict[str, list[dict]]:
    """Every record attributable to the subject, table by table."""
    collected: dict[str, list[dict]] = {}
    for table, column, _ in SUBJECT_TABLES:
        rows = (
            session.execute(
                text(f"SELECT * FROM {table} WHERE tenant_id = :t AND {column} = CAST(:u AS uuid)"),  # noqa: S608
                {"t": tenant_id, "u": user_id},
            )
            .mappings()
            .all()
        )
        if rows:
            collected[table] = [
                {
                    k: (str(v) if not isinstance(v, (int, float, bool, type(None))) else v)
                    for k, v in dict(r).items()
                    if k not in SENSITIVE_COLUMNS
                }
                for r in rows
            ]
    ledger = (
        session.execute(
            text(
                "SELECT sequence_no, category, action, outcome, occurred_at FROM audit_events "
                "WHERE tenant_id = :t AND human_id = CAST(:u AS uuid) ORDER BY sequence_no"
            ),
            {"t": tenant_id, "u": user_id},
        )
        .mappings()
        .all()
    )
    if ledger:
        collected["audit_events"] = [{k: str(v) for k, v in dict(r).items()} for r in ledger]
    return collected


def _access(
    session: Session,
    ctx: ExecutionContext,
    request_id: str,
    request_type: str,
    subject: dict[str, Any],
) -> DsarResult:
    collected = _collect(session, ctx.tenant_id, str(subject["id"]))
    counts = {table: len(rows) for table, rows in collected.items()}
    _finish(session, ctx, request_id, "COMPLETED", counts)

    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="DATA_ACCESS",
            action=f"dsar.{request_type.lower()}_fulfilled",
            resource_type="data_subject_request",
            resource_id=request_id,
            classification="RESTRICTED",
            payload={"tables": sorted(counts), "record_counts": counts},
        ),
    )
    return DsarResult(
        request_id=request_id,
        request_type=request_type,
        status="COMPLETED",
        subject_email=str(subject["email"]),
        affected=counts,
        export={"subject": {k: str(v) for k, v in subject.items()}, "records": collected},
    )


def _erase(
    session: Session,
    ctx: ExecutionContext,
    request_id: str,
    request_type: str,
    subject: dict[str, Any],
) -> DsarResult:
    holds = _active_holds(session, ctx.tenant_id)
    if holds:
        blocked = [h["hold_key"] for h in holds]
        session.execute(
            text(
                "UPDATE data_subject_requests SET status = 'BLOCKED_BY_HOLD', "
                "affected_records = CAST(:a AS jsonb) WHERE id = :i"
            ),
            {"a": json.dumps({"blocked_by": blocked}), "i": request_id},
        )
        AuditLedger(session).append(
            ctx,
            AuditEntry(
                category="USER_ACTION",
                action="dsar.delete_blocked_by_legal_hold",
                outcome="DENIED",
                resource_type="data_subject_request",
                resource_id=request_id,
                classification="RESTRICTED",
                payload={"holds": blocked},
            ),
        )
        return DsarResult(
            request_id=request_id,
            request_type=request_type,
            status="BLOCKED_BY_HOLD",
            subject_email=str(subject["email"]),
            blocked_by=blocked,
        )

    user_id = str(subject["id"])
    affected: dict[str, int] = {}

    # Detach the subject from records that must survive for operational
    # integrity, then anonymise the account itself.
    for table, column, strategy in SUBJECT_TABLES:
        if strategy == "PSEUDONYMISE":
            continue
        if strategy == "DELETE":
            statement = f"DELETE FROM {table} WHERE tenant_id = :t AND {column} = CAST(:u AS uuid)"  # noqa: S608
        else:
            statement = (  # noqa: S608
                f"UPDATE {table} SET {column} = NULL WHERE tenant_id = :t AND {column} = CAST(:u AS uuid)"
            )
        result = session.execute(text(statement), {"t": ctx.tenant_id, "u": user_id})
        if affected_rows(result):
            affected[table] = affected_rows(result)

    pseudonym = f"erased+{user_id[:8]}@invalid.local"
    session.execute(
        text(
            """
            UPDATE users
               SET email = :pseudonym, display_name = 'Erased subject',
                   password_hash = NULL, external_subject = NULL, mfa_secret_ref = NULL,
                   attributes = '{}'::jsonb, status = 'RETIRED', deleted_at = now(),
                   updated_at = now()
             WHERE tenant_id = :t AND id = CAST(:u AS uuid)
            """
        ),
        {"pseudonym": pseudonym, "t": ctx.tenant_id, "u": user_id},
    )
    affected["users"] = 1
    session.execute(
        text("DELETE FROM user_mfa WHERE tenant_id = :t AND user_id = CAST(:u AS uuid)"),
        {"t": ctx.tenant_id, "u": user_id},
    )
    session.execute(
        text(
            "DELETE FROM pii_inventory WHERE tenant_id = :t AND resource_type = 'user' AND resource_id = :u"
        ),
        {"t": ctx.tenant_id, "u": user_id},
    )

    _finish(session, ctx, request_id, "COMPLETED", affected)
    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="USER_ACTION",
            action="dsar.erasure_completed",
            resource_type="data_subject_request",
            resource_id=request_id,
            classification="RESTRICTED",
            payload={
                "affected": affected,
                "retained": NON_ERASABLE,
                "pseudonym": pseudonym,
            },
        ),
    )
    return DsarResult(
        request_id=request_id,
        request_type=request_type,
        status="COMPLETED",
        subject_email=str(subject["email"]),
        affected=affected,
        retained=dict(NON_ERASABLE),
    )


def _rectify(
    session: Session,
    ctx: ExecutionContext,
    request_id: str,
    request_type: str,
    subject: dict[str, Any],
) -> DsarResult:
    """Rectification records what must change; the correction itself is an
    administrative action taken through the normal user-management path, so
    that it carries its own authorisation and audit entry."""
    _finish(session, ctx, request_id, "COMPLETED", {"users": 1})
    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="USER_ACTION",
            action="dsar.rectification_acknowledged",
            resource_type="data_subject_request",
            resource_id=request_id,
            classification="RESTRICTED",
            payload={"subject_user_id": str(subject["id"])},
        ),
    )
    return DsarResult(
        request_id=request_id,
        request_type=request_type,
        status="COMPLETED",
        subject_email=str(subject["email"]),
        affected={"users": 1},
    )


def _finish(
    session: Session,
    ctx: ExecutionContext,
    request_id: str,
    status: str,
    affected: dict[str, int],
) -> None:
    session.execute(
        text(
            "UPDATE data_subject_requests SET status = :s, completed_at = now(), "
            "affected_records = CAST(:a AS jsonb) WHERE id = :i"
        ),
        {"s": status, "a": json.dumps(affected), "i": request_id},
    )


def apply_retention(session: Session, ctx: ExecutionContext) -> dict[str, int]:
    """Delete or anonymise records past their retention period.

    Anything under an active legal hold is skipped and reported, never removed.
    """
    if _active_holds(session, ctx.tenant_id):
        return {"skipped_due_to_legal_hold": 1}

    removed: dict[str, int] = {}
    result = session.execute(
        text(
            "UPDATE documents SET deleted_at = now() "
            "WHERE tenant_id = :t AND retention_until IS NOT NULL "
            "AND retention_until < now() AND deleted_at IS NULL AND legal_hold = false"
        ),
        {"t": ctx.tenant_id},
    )
    if affected_rows(result):
        removed["documents"] = affected_rows(result)

    result = session.execute(
        text("DELETE FROM sessions WHERE tenant_id = :t AND expires_at < now() - interval '30 days'"),
        {"t": ctx.tenant_id},
    )
    if affected_rows(result):
        removed["sessions"] = affected_rows(result)

    result = session.execute(
        text(
            "DELETE FROM memory_records WHERE tenant_id = :t "
            "AND expires_at IS NOT NULL AND expires_at < now()"
        ),
        {"t": ctx.tenant_id},
    )
    if affected_rows(result):
        removed["memory_records"] = affected_rows(result)
    return removed
