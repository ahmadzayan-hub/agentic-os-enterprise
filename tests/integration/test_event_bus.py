"""Transactional outbox: an event and its state change commit together."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext, HumanIdentity
from agentic_os.core.db import bind_tenant
from agentic_os.core.ids import prefixed_id
from agentic_os.runtime import events
from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


@pytest.fixture()
def ectx(tenant_id: str, organization_id: str) -> ExecutionContext:
    return ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(user_id="00000000-0000-0000-0000-000000000001", email="e@x.test"),
    )


@pytest.fixture(autouse=True)
def _clean_subscriptions():
    events.clear_subscriptions()
    yield
    events.clear_subscriptions()


@pytest.fixture(autouse=True)
def _drain_backlog(db: Session, tenant_id: str):
    """Clear the pending outbox before each test.

    Dispatch takes the oldest batch first, so a backlog accumulated by earlier
    tests would push this test's own event out of the batch and make the
    assertion depend on execution order.
    """
    db.execute(
        text(
            "UPDATE outbox_events SET status = 'DISPATCHED', dispatched_at = now() "
            "WHERE tenant_id = :t AND status IN ('PENDING', 'FAILED')"
        ),
        {"t": tenant_id},
    )
    db.commit()
    bind_tenant(db, tenant_id)
    yield


def test_outbox_commits_with_the_state_change(db: Session, ectx) -> None:
    """A rolled-back state change must leave no queued event behind."""
    marker = prefixed_id("task")
    db.execute(
        text("INSERT INTO tasks (tenant_id, title) VALUES (:t, :title)"),
        {"t": ectx.tenant_id, "title": marker},
    )
    events.publish(
        db, ectx, events.Event(event_type="Document.Uploaded", payload={"marker": marker})
    )
    db.rollback()

    task_rows = db.execute(
        text("SELECT count(*) FROM tasks WHERE tenant_id = :t AND title = :m"),
        {"t": ectx.tenant_id, "m": marker},
    ).scalar_one()
    outbox_rows = db.execute(
        text(
            "SELECT count(*) FROM outbox_events "
            "WHERE tenant_id = :t AND payload->>'marker' = :m"
        ),
        {"t": ectx.tenant_id, "m": marker},
    ).scalar_one()
    assert task_rows == 0
    assert outbox_rows == 0, "the event must not survive a rolled-back state change"


def test_published_event_is_queued_for_dispatch(db: Session, ectx) -> None:
    marker = prefixed_id("evt")
    events.publish(db, ectx, events.Event(event_type="Risk.Detected", payload={"marker": marker}))
    db.flush()
    row = db.execute(
        text(
            "SELECT status, attempts, event_type FROM outbox_events "
            "WHERE tenant_id = :t AND payload->>'marker' = :m"
        ),
        {"t": ectx.tenant_id, "m": marker},
    ).mappings().one()
    assert row["status"] == "PENDING"
    assert row["attempts"] == 0
    assert row["event_type"] == "Risk.Detected"


def test_dispatch_invokes_matching_handlers(db: Session, ectx) -> None:
    received: list[dict] = []
    events.subscribe("Invoice.*", lambda session, envelope: received.append(envelope))
    marker = prefixed_id("evt")
    events.publish(db, ectx, events.Event(event_type="Invoice.Paid", payload={"marker": marker}))
    db.flush()

    result = events.dispatch_pending(db, tenant_id=ectx.tenant_id, batch_size=100)
    assert result["dispatched"] >= 1
    assert any(e["payload"].get("marker") == marker for e in received)
    db.rollback()


def test_handler_failure_retries_with_backoff_then_dead_letters(db: Session, ectx) -> None:
    def always_fails(session, envelope):
        raise RuntimeError("handler is down")

    events.subscribe("Security.Alert", always_fails)
    marker = prefixed_id("evt")
    events.publish(
        db, ectx, events.Event(event_type="Security.Alert", payload={"marker": marker})
    )
    db.flush()
    db.execute(
        text(
            "UPDATE outbox_events SET max_attempts = 2 "
            "WHERE tenant_id = :t AND payload->>'marker' = :m"
        ),
        {"t": ectx.tenant_id, "m": marker},
    )

    events.dispatch_pending(db, tenant_id=ectx.tenant_id)
    first = db.execute(
        text(
            "SELECT status, attempts, last_error FROM outbox_events "
            "WHERE tenant_id = :t AND payload->>'marker' = :m"
        ),
        {"t": ectx.tenant_id, "m": marker},
    ).mappings().one()
    assert first["status"] == "FAILED"
    assert first["attempts"] == 1
    assert "handler is down" in first["last_error"]

    db.execute(
        text(
            "UPDATE outbox_events SET next_attempt_at = now() "
            "WHERE tenant_id = :t AND payload->>'marker' = :m"
        ),
        {"t": ectx.tenant_id, "m": marker},
    )
    events.dispatch_pending(db, tenant_id=ectx.tenant_id)
    second = db.execute(
        text(
            "SELECT status, attempts FROM outbox_events "
            "WHERE tenant_id = :t AND payload->>'marker' = :m"
        ),
        {"t": ectx.tenant_id, "m": marker},
    ).mappings().one()
    assert second["status"] == "DEAD"
    assert second["attempts"] == 2

    dead = events.dead_letters(db, ectx.tenant_id)
    assert any(d["event_type"] == "Security.Alert" for d in dead)
    db.rollback()


def test_dead_letters_can_be_replayed(db: Session, ectx) -> None:
    marker = prefixed_id("evt")
    events.publish(db, ectx, events.Event(event_type="Policy.Violated", payload={"marker": marker}))
    db.flush()
    outbox_id = db.execute(
        text(
            "SELECT id FROM outbox_events WHERE tenant_id = :t AND payload->>'marker' = :m"
        ),
        {"t": ectx.tenant_id, "m": marker},
    ).scalar_one()
    db.execute(
        text("UPDATE outbox_events SET status = 'DEAD', attempts = 8 WHERE id = :i"),
        {"i": outbox_id},
    )

    assert events.replay_dead_letter(db, ectx.tenant_id, str(outbox_id)) is True
    row = db.execute(
        text("SELECT status, attempts FROM outbox_events WHERE id = :i"), {"i": outbox_id}
    ).mappings().one()
    assert row["status"] == "PENDING"
    assert row["attempts"] == 0
    db.rollback()


def test_dispatch_is_tenant_scoped(db: Session, db_other: Session, ectx, other_tenant_id) -> None:
    marker = prefixed_id("evt")
    events.publish(db, ectx, events.Event(event_type="Agent.Started", payload={"marker": marker}))
    db.commit()
    # The tenant GUC is transaction-scoped, so the commit above cleared it.
    bind_tenant(db, ectx.tenant_id)

    events.dispatch_pending(db_other, tenant_id=other_tenant_id, batch_size=100)
    remaining = db.execute(
        text(
            "SELECT status FROM outbox_events WHERE tenant_id = :t AND payload->>'marker' = :m"
        ),
        {"t": ectx.tenant_id, "m": marker},
    ).scalar_one()
    assert remaining == "PENDING", "another tenant's dispatcher must not touch this event"


def test_wildcard_and_exact_subscriptions_both_match(db: Session, ectx) -> None:
    hits: list[str] = []
    events.subscribe("Approval.Granted", lambda s, e: hits.append("exact"))
    events.subscribe("Approval.*", lambda s, e: hits.append("wildcard"))
    events.publish(db, ectx, events.Event(event_type="Approval.Granted"))
    db.flush()
    events.dispatch_pending(db, tenant_id=ectx.tenant_id)
    assert set(hits) == {"exact", "wildcard"}
    db.rollback()
