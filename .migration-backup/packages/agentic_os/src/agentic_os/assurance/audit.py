"""Append-only, hash-chained audit ledger.

Every governed action writes one entry. Entries are linked by
``entry_hash = H(previous_hash || canonical(entry))`` so that removing or
altering any entry invalidates every subsequent link. The database refuses
UPDATE, DELETE and TRUNCATE on the ledger for all roles (migration 0006/0009),
so tamper-evidence does not depend on application discipline alone.

Payloads are redacted before they are written: the ledger records *that* a
secret-bearing parameter was passed and its fingerprint, never its value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext
from agentic_os.core.crypto import chain_hash, content_hash, hmac_sign
from agentic_os.core.ids import utcnow

AuditCategory = Literal[
    "AUTH",
    "AUTHZ",
    "USER_ACTION",
    "AGENT_ACTION",
    "APPROVAL",
    "POLICY",
    "MODEL_CALL",
    "TOOL_CALL",
    "DATA_ACCESS",
    "SECURITY",
    "CONFIG_CHANGE",
    "PROMPT_CHANGE",
    "WORKFLOW_CHANGE",
    "PRIVILEGE_CHANGE",
    "KILL_SWITCH",
    "EVIDENCE",
]

Outcome = Literal["SUCCESS", "DENIED", "FAILURE"]

#: Keys whose values are never written to the ledger in clear.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "private_key",
        "client_secret",
        "session_key",
        "mfa_secret",
        "cookie",
        "set-cookie",
    }
)

_SENSITIVE_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._\-]{16,}|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)

_REDACTED = "[REDACTED]"
_MAX_STRING = 2048


def redact_payload(value: Any, _depth: int = 0) -> Any:
    """Recursively strip secrets and bound the size of an audit payload."""
    if _depth > 12:
        return "[TRUNCATED_DEPTH]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                out[str(key)] = {
                    "redacted": True,
                    "fingerprint": content_hash(str(item))[:16],
                }
            else:
                out[str(key)] = redact_payload(item, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_payload(v, _depth + 1) for v in value[:200]]
    if isinstance(value, str):
        cleaned = _SENSITIVE_PATTERN.sub(_REDACTED, value)
        if len(cleaned) > _MAX_STRING:
            return cleaned[:_MAX_STRING] + f"...[truncated {len(cleaned) - _MAX_STRING} chars]"
        return cleaned
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_payload(str(value), _depth + 1)


@dataclass(slots=True)
class AuditEntry:
    category: AuditCategory
    action: str
    outcome: Outcome = "SUCCESS"
    resource_type: str = ""
    resource_id: str = ""
    classification: str = "INTERNAL"
    payload: dict[str, Any] = field(default_factory=dict)


class AuditLedger:
    """Writer and verifier for the tenant-scoped audit chain."""

    def __init__(self, session: Session, *, signing_key: bytes | None = None) -> None:
        self._session = session
        self._signing_key = signing_key

    def append(self, ctx: ExecutionContext, entry: AuditEntry) -> dict[str, Any]:
        """Append one entry and return its ledger coordinates."""
        row = self._session.execute(
            text("SELECT next_no, prev_hash FROM audit_next_sequence(:tenant)"),
            {"tenant": ctx.tenant_id},
        ).one()
        sequence_no, previous_hash = int(row.next_no), str(row.prev_hash)

        identities = ctx.audit_identities()
        occurred_at = utcnow()
        body = {
            "tenant_id": ctx.tenant_id,
            "sequence_no": sequence_no,
            "category": entry.category,
            "action": entry.action,
            "outcome": entry.outcome,
            "resource_type": entry.resource_type,
            "resource_id": entry.resource_id,
            "correlation_id": ctx.correlation_id,
            "run_id": ctx.run_id or None,
            "classification": entry.classification,
            "identities": identities,
            "payload": redact_payload(entry.payload),
            "occurred_at": occurred_at.isoformat(),
        }
        entry_hash = chain_hash(previous_hash, body)
        signature = hmac_sign(self._signing_key, body) if self._signing_key else ""

        self._session.execute(
            text(
                """
                INSERT INTO audit_events (
                  tenant_id, sequence_no, category, action, outcome,
                  human_id, agent_id, agent_version, workflow_run_id, tool_id,
                  service_principal, resource_type, resource_id, correlation_id,
                  run_id, classification, payload, previous_hash, entry_hash,
                  signature, occurred_at
                ) VALUES (
                  :tenant_id, :sequence_no, :category, :action, :outcome,
                  :human_id, :agent_id, :agent_version, :workflow_run_id, :tool_id,
                  :service_principal, :resource_type, :resource_id, :correlation_id,
                  :run_id, :classification, CAST(:payload AS jsonb), :previous_hash,
                  :entry_hash, :signature, :occurred_at
                )
                """
            ),
            {
                "tenant_id": ctx.tenant_id,
                "sequence_no": sequence_no,
                "category": entry.category,
                "action": entry.action,
                "outcome": entry.outcome,
                "human_id": identities["human_id"],
                "agent_id": identities["agent_id"],
                "agent_version": identities["agent_version"],
                "workflow_run_id": identities["workflow_run_id"],
                "tool_id": identities["tool_id"],
                "service_principal": identities["service_principal"],
                "resource_type": entry.resource_type,
                "resource_id": entry.resource_id,
                "correlation_id": ctx.correlation_id,
                "run_id": ctx.run_id or None,
                "classification": entry.classification,
                "payload": _json(body["payload"]),
                "previous_hash": previous_hash,
                "entry_hash": entry_hash,
                "signature": signature,
                "occurred_at": occurred_at,
            },
        )
        return {"sequence_no": sequence_no, "entry_hash": entry_hash}

    def verify_chain(self, tenant_id: str) -> dict[str, Any]:
        """Recompute the chain in the database and report the first break."""
        row = self._session.execute(
            text("SELECT checked, broken_at, expected_prev, found_prev FROM audit_verify_chain(:t)"),
            {"t": tenant_id},
        ).one()
        return {
            "entries_checked": int(row.checked),
            "intact": row.broken_at is None,
            "broken_at": int(row.broken_at) if row.broken_at is not None else None,
            "expected_previous_hash": row.expected_prev,
            "found_previous_hash": row.found_prev,
        }

    def recent(self, tenant_id: str, *, limit: int = 100, category: str | None = None) -> list[dict]:
        sql = """
            SELECT sequence_no, category, action, outcome, resource_type, resource_id,
                   human_id, agent_id, tool_id, correlation_id, run_id, classification,
                   payload, entry_hash, occurred_at
            FROM audit_events
            WHERE tenant_id = :tenant
              AND (:category IS NULL OR category = :category)
            ORDER BY sequence_no DESC
            LIMIT :limit
        """
        rows = self._session.execute(
            text(sql), {"tenant": tenant_id, "category": category, "limit": min(limit, 1000)}
        ).mappings()
        return [dict(r) for r in rows]


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def audit(session: Session, ctx: ExecutionContext, entry: AuditEntry) -> dict[str, Any]:
    """Convenience wrapper for a single ledger append."""
    return AuditLedger(session).append(ctx, entry)
