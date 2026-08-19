"""The background worker moves durable work forward without a request.

The worker owns its own sessions and commits, so this test commits its setup
and cleans up after itself rather than relying on the rolled-back fixture.
"""

from __future__ import annotations

import agentic_os.runtime.steps  # noqa: F401 - registers step handlers
import pytest
from agentic_os.core.context import ExecutionContext
from agentic_os.core.db import bind_tenant, get_session_factory
from agentic_os.core.ids import prefixed_id
from agentic_os.runtime import workflow_engine as we
from agentic_os.worker.loop import WorkerConfig, tick
from sqlalchemy import text

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


@pytest.fixture()
def committed(tenant_id: str, organization_id: str):
    """A committed workflow run, removed again when the test finishes."""
    factory = get_session_factory()
    session = factory()
    bind_tenant(session, tenant_id, actor="worker-test")
    ctx = ExecutionContext(tenant_id=tenant_id, organization_id=organization_id)
    key = prefixed_id("wf_worker")
    we.register_workflow(
        session,
        ctx,
        workflow_key=key,
        name=key,
        definition={
            "steps": [
                {"key": "one", "type": "NOOP"},
                {"key": "two", "type": "NOOP"},
                {"key": "three", "type": "NOOP"},
            ]
        },
        owner_team="test",
    )
    run_id = we.start(session, ctx, key, {"input": 1})
    session.commit()
    try:
        yield {"session": session, "ctx": ctx, "run_id": run_id, "workflow_key": key}
    finally:
        _refresh(session, tenant_id)
        session.execute(
            text("DELETE FROM workflow_runs WHERE tenant_id = :t AND id = CAST(:i AS uuid)"),
            {"t": tenant_id, "i": run_id},
        )
        session.execute(
            text("DELETE FROM workflows WHERE tenant_id = :t AND workflow_key = :k"),
            {"t": tenant_id, "k": key},
        )
        session.commit()
        session.close()


def _refresh(session, tenant_id: str) -> None:
    """End the read snapshot and re-bind the tenant.

    ``app.tenant_id`` is set transaction-locally on purpose, so a commit or
    rollback clears it. Re-binding is required before the next read, otherwise
    RLS correctly hides every row.
    """
    session.rollback()
    bind_tenant(session, tenant_id, actor="worker-test")


def _status(session, tenant_id: str, run_id: str) -> str:
    _refresh(session, tenant_id)
    return session.execute(
        text("SELECT status FROM workflow_runs WHERE tenant_id = :t AND id = CAST(:i AS uuid)"),
        {"t": tenant_id, "i": run_id},
    ).scalar_one()


def test_the_worker_advances_a_pending_run_to_completion(committed, tenant_id):
    session, run_id = committed["session"], committed["run_id"]
    config = WorkerConfig(worker_id="pytest-worker", max_passes=1)

    assert _status(session, tenant_id, run_id) == "PENDING"

    for _ in range(6):
        result = tick(config)
        assert result.errors == [], result.errors
        if _status(session, tenant_id, run_id) == "SUCCEEDED":
            break

    assert _status(session, tenant_id, run_id) == "SUCCEEDED"

    steps = (
        session.execute(
            text(
                "SELECT status FROM workflow_steps WHERE workflow_run_id = CAST(:i AS uuid) "
                "ORDER BY step_index"
            ),
            {"i": run_id},
        )
        .scalars()
        .all()
    )
    assert steps and all(status == "SUCCEEDED" for status in steps)


def test_a_pass_covers_every_active_tenant(committed):
    """The worker binds each tenant in turn; RLS gives it no way to sweep."""
    result = tick(WorkerConfig(worker_id="pytest-worker", max_passes=1))
    assert result.tenants >= 2
    assert result.errors == []


def test_the_worker_drains_the_outbox(committed, tenant_id):
    """Starting a run publishes an event; the worker is what delivers it."""
    session = committed["session"]
    for _ in range(6):
        tick(WorkerConfig(worker_id="pytest-worker", max_passes=1))
        _refresh(session, tenant_id)
        pending = session.execute(
            text(
                "SELECT count(*) FROM outbox_events WHERE tenant_id = :t "
                "AND status IN ('PENDING', 'FAILED') AND next_attempt_at <= now()"
            ),
            {"t": tenant_id},
        ).scalar_one()
        if pending == 0:
            break
    assert pending == 0
