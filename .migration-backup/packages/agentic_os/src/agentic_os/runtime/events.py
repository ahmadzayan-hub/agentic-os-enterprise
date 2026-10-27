"""Event bus with a transactional outbox.

An event is written in the *same transaction* as the state change that caused
it, so the two cannot diverge: either both commit or neither does. A separate
dispatcher then delivers at-least-once with exponential backoff, and gives up
into a dead-letter state rather than retrying forever.

Consumers must be idempotent. The event id is stable across redeliveries so a
consumer can deduplicate on it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext
from agentic_os.core.db import affected_rows

#: Canonical event types. Publishing an unknown type is allowed but logged, so
#: the taxonomy can grow without a deployment while staying visible.
KNOWN_EVENT_TYPES = frozenset(
    {
        "Customer.Created",
        "Customer.Updated",
        "Invoice.Created",
        "Invoice.Paid",
        "Invoice.Overdue",
        "Document.Uploaded",
        "Document.Processed",
        "Document.Approved",
        "Document.Rejected",
        "Email.Received",
        "Risk.Detected",
        "Agent.Started",
        "Agent.Completed",
        "Agent.Failed",
        "Run.Started",
        "Run.Completed",
        "Run.Failed",
        "Workflow.Started",
        "Workflow.Completed",
        "Workflow.Failed",
        "Workflow.StepFailed",
        "Approval.Required",
        "Approval.Granted",
        "Approval.Rejected",
        "Approval.Expired",
        "Policy.Violated",
        "Security.Alert",
        "Dataset.Ingested",
        "KillSwitch.Engaged",
        "KillSwitch.Released",
        "Budget.Threshold",
        "Budget.Exceeded",
    }
)

MAX_DELIVERY_ATTEMPTS = 8
BASE_BACKOFF_SECONDS = 2


@dataclass(slots=True)
class Event:
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    aggregate_type: str = ""
    aggregate_id: str = ""
    causation_id: str = ""


def publish(session: Session, ctx: ExecutionContext, event: Event) -> str:
    """Record an event and queue it for delivery, in the caller's transaction."""
    row = session.execute(
        text(
            """
            INSERT INTO events (tenant_id, event_type, aggregate_type, aggregate_id,
                                correlation_id, causation_id, payload)
            VALUES (:t, :type, :atype, :aid, :corr, :caus, CAST(:payload AS jsonb))
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id,
            "type": event.event_type,
            "atype": event.aggregate_type,
            "aid": event.aggregate_id,
            "corr": ctx.correlation_id,
            "caus": event.causation_id,
            "payload": json.dumps(event.payload, default=str),
        },
    ).one()
    event_id = str(row.id)

    session.execute(
        text(
            """
            INSERT INTO outbox_events (tenant_id, event_id, event_type, payload, correlation_id,
                                       max_attempts)
            VALUES (:t, :eid, :type, CAST(:payload AS jsonb), :corr, :max_attempts)
            """
        ),
        {
            "t": ctx.tenant_id,
            "eid": event_id,
            "type": event.event_type,
            "payload": json.dumps(event.payload, default=str),
            "corr": ctx.correlation_id,
            "max_attempts": MAX_DELIVERY_ATTEMPTS,
        },
    )
    return event_id


#: In-process handler registry. A production deployment additionally forwards
#: to a broker; the outbox contract is identical either way.
_HANDLERS: dict[str, list[Callable[[Session, dict[str, Any]], None]]] = {}


def subscribe(pattern: str, handler: Callable[[Session, dict[str, Any]], None]) -> None:
    """Register an in-process handler. ``pattern`` may end with ``*``."""
    _HANDLERS.setdefault(pattern, []).append(handler)


def clear_subscriptions() -> None:
    _HANDLERS.clear()


def _matching_handlers(event_type: str) -> list[Callable[[Session, dict[str, Any]], None]]:
    handlers: list[Callable] = []
    for pattern, registered in _HANDLERS.items():
        if pattern == event_type or (pattern.endswith("*") and event_type.startswith(pattern[:-1])):
            handlers.extend(registered)
    return handlers


def dispatch_pending(session: Session, *, tenant_id: str, batch_size: int = 50) -> dict[str, int]:
    """Deliver a batch of due outbox entries for one tenant.

    ``FOR UPDATE SKIP LOCKED`` means two dispatchers never pick the same row,
    so the worker scales horizontally without coordination. Tenant-scoped for
    the same reason as the workflow claim: RLS has no bypass, and per-tenant
    batches keep one busy tenant from starving the rest.
    """
    rows = (
        session.execute(
            text(
                """
            SELECT id, tenant_id, event_type, payload, attempts, max_attempts
            FROM outbox_events
            WHERE tenant_id = CAST(:tenant AS uuid)
              AND status IN ('PENDING', 'FAILED') AND next_attempt_at <= now()
            ORDER BY created_at
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
            """
            ),
            {"limit": batch_size, "tenant": tenant_id},
        )
        .mappings()
        .all()
    )

    dispatched = failed = dead = 0
    for row in rows:
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        envelope = {
            "outbox_id": str(row["id"]),
            "tenant_id": str(row["tenant_id"]),
            "event_type": row["event_type"],
            "payload": payload,
        }
        try:
            for handler in _matching_handlers(row["event_type"]):
                handler(session, envelope)
            session.execute(
                text(
                    "UPDATE outbox_events SET status = 'DISPATCHED', dispatched_at = now(), "
                    "attempts = attempts + 1 WHERE id = :i"
                ),
                {"i": row["id"]},
            )
            dispatched += 1
        except Exception as exc:  # noqa: BLE001 - a handler failure must not stop the batch
            attempts = int(row["attempts"]) + 1
            if attempts >= int(row["max_attempts"]):
                session.execute(
                    text(
                        "UPDATE outbox_events SET status = 'DEAD', attempts = :a, last_error = :e "
                        "WHERE id = :i"
                    ),
                    {"a": attempts, "e": str(exc)[:2000], "i": row["id"]},
                )
                dead += 1
            else:
                backoff = BASE_BACKOFF_SECONDS * (2 ** (attempts - 1))
                session.execute(
                    text(
                        "UPDATE outbox_events SET status = 'FAILED', attempts = :a, "
                        "last_error = :e, next_attempt_at = now() + make_interval(secs => :b) "
                        "WHERE id = :i"
                    ),
                    {"a": attempts, "e": str(exc)[:2000], "b": backoff, "i": row["id"]},
                )
                failed += 1

    return {"selected": len(rows), "dispatched": dispatched, "retrying": failed, "dead": dead}


def dead_letters(session: Session, tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            "SELECT id, event_type, attempts, last_error, created_at FROM outbox_events "
            "WHERE tenant_id = :t AND status = 'DEAD' ORDER BY created_at DESC LIMIT :l"
        ),
        {"t": tenant_id, "l": limit},
    ).mappings()
    return [dict(r) for r in rows]


def replay_dead_letter(session: Session, tenant_id: str, outbox_id: str) -> bool:
    result = session.execute(
        text(
            "UPDATE outbox_events SET status = 'PENDING', attempts = 0, "
            "next_attempt_at = now(), last_error = '' "
            "WHERE tenant_id = :t AND id = CAST(:i AS uuid) AND status = 'DEAD'"
        ),
        {"t": tenant_id, "i": outbox_id},
    )
    return affected_rows(result) > 0


def backlog(session: Session, tenant_id: str) -> dict[str, int]:
    rows = session.execute(
        text("SELECT status, count(*) AS n FROM outbox_events WHERE tenant_id = :t GROUP BY status"),
        {"t": tenant_id},
    ).all()
    return {str(r.status): int(r.n) for r in rows}
