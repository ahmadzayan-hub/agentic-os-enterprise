"""Durable workflow behaviour: retries, idempotency, compensation, DLQ, approval."""

from __future__ import annotations

import json

import agentic_os.runtime.steps  # noqa: F401 - registers step handlers
import pytest
from agentic_os.core.context import ExecutionContext, HumanIdentity
from agentic_os.core.db import bind_tenant
from agentic_os.core.errors import Conflict, ValidationError
from agentic_os.core.ids import prefixed_id
from agentic_os.runtime import workflow_engine as we
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


@pytest.fixture()
def wctx(db: Session, tenant_id: str, organization_id: str) -> ExecutionContext:
    user = db.execute(
        text("SELECT id FROM users WHERE tenant_id = :t AND email = 'admin@rta.example'"),
        {"t": tenant_id},
    ).one()
    return ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(
            user_id=str(user.id),
            email="admin@rta.example",
            roles=frozenset({"platform_admin", "approver"}),
            permissions=frozenset({"*"}),
            clearance="RESTRICTED",
            mfa_satisfied=True,
        ),
    )


def _register(db: Session, ctx: ExecutionContext, key: str, steps: list[dict]) -> str:
    we.register_workflow(db, ctx, workflow_key=key, name=key, definition={"steps": steps}, owner_team="test")
    return key


# ------------------------------------------------------------------ validation
def test_definition_requires_steps() -> None:
    assert we.validate_definition({"steps": []}) == ["workflow definition must contain at least one step"]


def test_definition_rejects_unknown_step_type() -> None:
    problems = we.validate_definition({"steps": [{"key": "a", "type": "TELEPORT"}]})
    assert any("unknown type" in p for p in problems)


def test_definition_rejects_duplicate_keys() -> None:
    problems = we.validate_definition({"steps": [{"key": "a", "type": "NOOP"}, {"key": "a", "type": "NOOP"}]})
    assert any("duplicate step key" in p for p in problems)


def test_registering_an_invalid_definition_raises(db: Session, wctx) -> None:
    with pytest.raises(ValidationError):
        we.register_workflow(
            db,
            wctx,
            workflow_key="bad",
            name="bad",
            definition={"steps": [{"key": "x", "type": "NOPE"}]},
        )


# ------------------------------------------------------------------- happy path
def test_workflow_runs_to_completion(db: Session, wctx) -> None:
    key = _register(
        db,
        wctx,
        prefixed_id("wf_ok"),
        [
            {"key": "first", "type": "NOOP"},
            {"key": "second", "type": "NOOP"},
            {"key": "third", "type": "NOOP"},
        ],
    )
    run_id = we.start(db, wctx, key, {"input": 1})
    state = we.run_to_completion(db, wctx, run_id)
    assert state.status == "SUCCEEDED"
    assert state.state["first"]["noop"] is True
    assert state.state["third"]["key"] == "third"

    steps = (
        db.execute(
            text("SELECT status FROM workflow_steps WHERE workflow_run_id = CAST(:i AS uuid)"),
            {"i": run_id},
        )
        .scalars()
        .all()
    )
    assert all(s == "SUCCEEDED" for s in steps)


def test_start_is_idempotent(db: Session, wctx) -> None:
    key = _register(db, wctx, prefixed_id("wf_idem"), [{"key": "a", "type": "NOOP"}])
    idem = prefixed_id("idem")
    first = we.start(db, wctx, key, {}, idempotency_key=idem)
    second = we.start(db, wctx, key, {}, idempotency_key=idem)
    assert first == second


def test_completed_step_is_never_re_executed(db: Session, wctx) -> None:
    """Re-advancing a run must not repeat work that already produced a result."""
    key = _register(
        db, wctx, prefixed_id("wf_once"), [{"key": "a", "type": "NOOP"}, {"key": "b", "type": "NOOP"}]
    )
    run_id = we.start(db, wctx, key, {})
    we.advance(db, wctx, run_id)  # completes 'a'
    attempts_before = db.execute(
        text(
            "SELECT attempt FROM workflow_steps WHERE workflow_run_id = CAST(:i AS uuid) AND step_key = 'a'"
        ),
        {"i": run_id},
    ).scalar_one()

    db.execute(
        text("UPDATE workflow_runs SET current_step = 0 WHERE id = CAST(:i AS uuid)"),
        {"i": run_id},
    )
    we.advance(db, wctx, run_id)
    attempts_after = db.execute(
        text(
            "SELECT attempt FROM workflow_steps WHERE workflow_run_id = CAST(:i AS uuid) AND step_key = 'a'"
        ),
        {"i": run_id},
    ).scalar_one()
    assert attempts_after == attempts_before


# --------------------------------------------------------------------- retries
def test_retryable_failure_retries_then_dead_letters(db: Session, wctx) -> None:
    key = _register(
        db,
        wctx,
        prefixed_id("wf_retry"),
        [
            {
                "key": "flaky",
                "type": "FAIL",
                "max_attempts": 3,
                "backoff_seconds": 0,
                "input": {"error_class": "UPSTREAM_UNAVAILABLE", "message": "upstream down"},
            }
        ],
    )
    run_id = we.start(db, wctx, key, {})
    for _ in range(6):
        state = we.advance(db, wctx, run_id)
        db.execute(
            text("UPDATE workflow_runs SET next_poll_at = now() WHERE id = CAST(:i AS uuid)"),
            {"i": run_id},
        )
        if state.status in ("FAILED", "COMPENSATED"):
            break

    assert state.status in ("FAILED", "COMPENSATED")
    attempts = db.execute(
        text("SELECT attempt FROM workflow_steps WHERE workflow_run_id = CAST(:i AS uuid)"),
        {"i": run_id},
    ).scalar_one()
    assert attempts == 3, "should exhaust exactly max_attempts"

    dead = db.execute(
        text("SELECT count(*) FROM workflow_dead_letters WHERE workflow_run_id = CAST(:i AS uuid)"),
        {"i": run_id},
    ).scalar_one()
    assert dead == 1


def test_non_retryable_failure_does_not_retry(db: Session, wctx) -> None:
    key = _register(
        db,
        wctx,
        prefixed_id("wf_hard"),
        [
            {
                "key": "bad",
                "type": "FAIL",
                "max_attempts": 5,
                "input": {"error_class": "VALIDATION", "message": "not retryable"},
            }
        ],
    )
    run_id = we.start(db, wctx, key, {})
    state = we.advance(db, wctx, run_id)
    assert state.status in ("FAILED", "COMPENSATED")
    attempts = db.execute(
        text("SELECT attempt FROM workflow_steps WHERE workflow_run_id = CAST(:i AS uuid)"),
        {"i": run_id},
    ).scalar_one()
    assert attempts == 1


# ---------------------------------------------------------------- compensation
def test_failure_compensates_completed_steps_in_reverse(db: Session, wctx) -> None:
    key = _register(
        db,
        wctx,
        prefixed_id("wf_comp"),
        [
            {
                "key": "reserve",
                "type": "TASK",
                "input": {"title": "reserve capacity"},
                "compensation": {
                    "key": "release",
                    "type": "TASK",
                    "input": {"title": "COMPENSATION: release capacity"},
                },
            },
            {
                "key": "boom",
                "type": "FAIL",
                "max_attempts": 1,
                "input": {"error_class": "VALIDATION", "message": "downstream rejected"},
            },
        ],
    )
    run_id = we.start(db, wctx, key, {})
    state = we.run_to_completion(db, wctx, run_id)

    assert state.status == "COMPENSATED"
    assert state.state["_compensated_steps"] == ["reserve"]
    compensation_tasks = db.execute(
        text("SELECT count(*) FROM tasks WHERE tenant_id = :t AND title LIKE 'COMPENSATION:%'"),
        {"t": wctx.tenant_id},
    ).scalar_one()
    assert compensation_tasks >= 1

    compensated = db.execute(
        text(
            "SELECT compensated FROM workflow_steps "
            "WHERE workflow_run_id = CAST(:i AS uuid) AND step_key = 'reserve'"
        ),
        {"i": run_id},
    ).scalar_one()
    assert compensated is True


# -------------------------------------------------------------------- approval
def test_approval_step_parks_the_run_and_resumes_on_approval(db: Session, wctx) -> None:
    from agentic_os.control.approval_engine import decide

    key = _register(
        db,
        wctx,
        prefixed_id("wf_appr"),
        [
            {
                "key": "gate",
                "type": "APPROVAL",
                "input": {
                    "action": "operations.close_work_order",
                    "reason": "closing WO-4471 after inspection",
                    "consequences": "the work order is marked complete in the system of record",
                },
            },
            {"key": "after", "type": "NOOP"},
        ],
    )
    run_id = we.start(db, wctx, key, {})
    state = we.run_to_completion(db, wctx, run_id)
    assert state.status == "AWAITING_APPROVAL"

    paused = (
        db.execute(
            text("SELECT paused, state FROM workflow_runs WHERE id = CAST(:i AS uuid)"),
            {"i": run_id},
        )
        .mappings()
        .one()
    )
    assert paused["paused"] is True
    approval_id = (paused["state"] if isinstance(paused["state"], dict) else json.loads(paused["state"]))[
        "_awaiting_approval_id"
    ]

    decide(db, wctx, approval_id, "APPROVED", comment="inspection evidence reviewed")
    assert we.resume(db, wctx, run_id) is True

    state = we.run_to_completion(db, wctx, run_id)
    assert state.status == "SUCCEEDED"
    assert state.state["after"]["noop"] is True


def test_rejected_approval_fails_the_run(db: Session, wctx) -> None:
    from agentic_os.control.approval_engine import decide

    key = _register(
        db,
        wctx,
        prefixed_id("wf_reject"),
        [
            {
                "key": "gate",
                "type": "APPROVAL",
                "max_attempts": 1,
                "input": {
                    "action": "assets.decommission",
                    "reason": "obsolete",
                    "consequences": "the asset is permanently removed",
                },
            },
            {"key": "after", "type": "NOOP"},
        ],
    )
    run_id = we.start(db, wctx, key, {})
    we.run_to_completion(db, wctx, run_id)
    row = db.execute(
        text("SELECT state FROM workflow_runs WHERE id = CAST(:i AS uuid)"), {"i": run_id}
    ).scalar_one()
    approval_id = (row if isinstance(row, dict) else json.loads(row))["_awaiting_approval_id"]

    decide(db, wctx, approval_id, "REJECTED", comment="safety case not satisfied")
    we.resume(db, wctx, run_id)
    state = we.run_to_completion(db, wctx, run_id)
    assert state.status in ("FAILED", "COMPENSATED")


# ------------------------------------------------------------------- lifecycle
def test_cancel_stops_the_run(db: Session, wctx) -> None:
    key = _register(
        db, wctx, prefixed_id("wf_cancel"), [{"key": "a", "type": "NOOP"}, {"key": "b", "type": "NOOP"}]
    )
    run_id = we.start(db, wctx, key, {})
    assert we.cancel(db, wctx, run_id) is True
    state = we.advance(db, wctx, run_id)
    assert state.status == "CANCELLED"


def test_concurrency_limit_is_enforced(db: Session, wctx) -> None:
    key = prefixed_id("wf_conc")
    we.register_workflow(
        db,
        wctx,
        workflow_key=key,
        name=key,
        definition={"steps": [{"key": "a", "type": "NOOP"}]},
        max_concurrent_runs=2,
    )
    we.start(db, wctx, key, {})
    we.start(db, wctx, key, {})
    with pytest.raises(Conflict) as excinfo:
        we.start(db, wctx, key, {})
    assert excinfo.value.details["limit"] == 2


def test_claim_leases_runs_exclusively(db: Session, wctx) -> None:
    key = _register(db, wctx, prefixed_id("wf_lease"), [{"key": "a", "type": "NOOP"}])
    run_id = we.start(db, wctx, key, {})
    db.commit()
    bind_tenant(db, wctx.tenant_id)

    first = we.claim_due_runs(db, "worker-1", tenant_id=wctx.tenant_id, limit=50)
    assert run_id in first
    second = we.claim_due_runs(db, "worker-2", tenant_id=wctx.tenant_id, limit=50)
    assert run_id not in second, "a leased run must not be claimable by a second worker"


def test_claim_is_tenant_scoped(db: Session, wctx, other_tenant_id: str) -> None:
    """A worker sweeping one tenant must never lease another tenant's run."""
    key = _register(db, wctx, prefixed_id("wf_scope"), [{"key": "a", "type": "NOOP"}])
    run_id = we.start(db, wctx, key, {})
    db.commit()
    bind_tenant(db, other_tenant_id)
    claimed = we.claim_due_runs(db, "worker-x", tenant_id=other_tenant_id, limit=50)
    assert run_id not in claimed
    bind_tenant(db, wctx.tenant_id)
