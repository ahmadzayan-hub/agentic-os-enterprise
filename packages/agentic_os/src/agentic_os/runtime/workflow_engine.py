"""Durable workflow engine.

State lives in the database, not in a process. A worker leases a run, advances
it by one step, and commits; if the worker dies the lease expires and another
worker resumes from exactly where it stopped. Nothing is held in memory between
steps, so a restart loses nothing.

Guarantees:

* **Idempotency** — every step carries a key unique per tenant, and a step that
  has already produced a result is never re-executed.
* **Bounded retry** — failures retry with exponential backoff up to the step's
  limit, then either compensate or dead-letter.
* **Compensation** — a failed run runs the compensating action of every
  completed step, newest first, so partial work is undone in reverse order.
* **Human steps** — an approval step parks the run; it consumes no worker while
  waiting and resumes when the approval resolves.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext, WorkflowIdentity
from agentic_os.core.crypto import content_hash
from agentic_os.core.errors import AgenticError, Conflict, NotFound, ValidationError
from agentic_os.core.ids import utcnow
from agentic_os.runtime.events import Event, publish

LEASE_SECONDS = 60
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_TIMEOUT_SECONDS = 120

#: Step executors, keyed by step type. A definition naming an unregistered type
#: fails validation rather than at runtime.
StepHandler = Callable[[Session, ExecutionContext, dict[str, Any], dict[str, Any]], dict[str, Any]]
_STEP_HANDLERS: dict[str, StepHandler] = {}


def register_step_type(name: str) -> Callable[[StepHandler], StepHandler]:
    def decorator(handler: StepHandler) -> StepHandler:
        _STEP_HANDLERS[name] = handler
        return handler

    return decorator


def registered_step_types() -> frozenset[str]:
    return frozenset(_STEP_HANDLERS)


# ---------------------------------------------------------------------------
# Definition
# ---------------------------------------------------------------------------
def validate_definition(definition: dict[str, Any]) -> list[str]:
    """Static validation of a workflow definition."""
    problems: list[str] = []
    steps = definition.get("steps")
    if not isinstance(steps, list) or not steps:
        return ["workflow definition must contain at least one step"]

    keys: set[str] = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            problems.append(f"step {index} is not an object")
            continue
        key = step.get("key")
        if not key:
            problems.append(f"step {index} has no key")
        elif key in keys:
            problems.append(f"duplicate step key '{key}'")
        else:
            keys.add(key)

        step_type = step.get("type")
        if step_type not in _STEP_HANDLERS:
            problems.append(
                f"step '{key}' has unknown type '{step_type}'; registered types are {sorted(_STEP_HANDLERS)}"
            )
        if int(step.get("max_attempts", DEFAULT_MAX_ATTEMPTS)) < 1:
            problems.append(f"step '{key}' has max_attempts below 1")
        compensates = step.get("compensates")
        if compensates is not None and compensates not in keys:
            problems.append(f"step '{key}' compensates '{compensates}', which does not precede it")
    return problems


def register_workflow(
    session: Session,
    ctx: ExecutionContext,
    *,
    workflow_key: str,
    name: str,
    definition: dict[str, Any],
    description: str = "",
    owner_team: str = "",
    max_concurrent_runs: int = 10,
) -> dict[str, Any]:
    problems = validate_definition(definition)
    if problems:
        raise ValidationError(
            f"workflow '{workflow_key}' definition is invalid", details={"problems": problems}
        )

    row = session.execute(
        text(
            """
            INSERT INTO workflows (tenant_id, workflow_key, name, description, owner_team,
                                   max_concurrent_runs)
            VALUES (:t, :k, :n, :d, :o, :mc)
            ON CONFLICT (tenant_id, workflow_key) DO UPDATE
              SET name = EXCLUDED.name, description = EXCLUDED.description,
                  owner_team = EXCLUDED.owner_team,
                  max_concurrent_runs = EXCLUDED.max_concurrent_runs,
                  current_version = workflows.current_version + 1
            RETURNING id, current_version
            """
        ),
        {
            "t": ctx.tenant_id,
            "k": workflow_key,
            "n": name,
            "d": description,
            "o": owner_team,
            "mc": max_concurrent_runs,
        },
    ).one()

    session.execute(
        text(
            """
            INSERT INTO workflow_versions (tenant_id, workflow_id, version, definition,
                                           definition_hash)
            VALUES (:t, :w, :v, CAST(:def AS jsonb), :h)
            ON CONFLICT (workflow_id, version) DO UPDATE
              SET definition = EXCLUDED.definition, definition_hash = EXCLUDED.definition_hash
            """
        ),
        {
            "t": ctx.tenant_id,
            "w": row.id,
            "v": row.current_version,
            "def": json.dumps(definition, default=str),
            "h": content_hash(definition),
        },
    )
    return {"workflow_id": str(row.id), "version": int(row.current_version)}


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class WorkflowRunState:
    workflow_run_id: str
    status: str
    current_step: int
    state: dict[str, Any]
    output: dict[str, Any] | None = None
    error_class: str = ""
    error_message: str = ""


def start(
    session: Session,
    ctx: ExecutionContext,
    workflow_key: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
    run_id: str | None = None,
    parent_workflow_run_id: str | None = None,
    deadline_seconds: int = 3600,
) -> str:
    workflow = (
        session.execute(
            text(
                "SELECT id, current_version, max_concurrent_runs, status FROM workflows "
                "WHERE tenant_id = :t AND workflow_key = :k"
            ),
            {"t": ctx.tenant_id, "k": workflow_key},
        )
        .mappings()
        .first()
    )
    if workflow is None:
        raise NotFound(f"workflow '{workflow_key}' is not registered")
    if workflow["status"] != "ACTIVE":
        raise Conflict(f"workflow '{workflow_key}' is {workflow['status']}")

    if idempotency_key:
        existing = session.execute(
            text(
                "SELECT id FROM workflow_runs WHERE tenant_id = :t AND workflow_id = :w "
                "AND idempotency_key = :i"
            ),
            {"t": ctx.tenant_id, "w": workflow["id"], "i": idempotency_key},
        ).first()
        if existing is not None:
            return str(existing.id)

    in_flight = session.execute(
        text(
            "SELECT count(*) FROM workflow_runs WHERE tenant_id = :t AND workflow_id = :w "
            "AND status IN ('PENDING', 'RUNNING')"
        ),
        {"t": ctx.tenant_id, "w": workflow["id"]},
    ).scalar_one()
    if in_flight >= int(workflow["max_concurrent_runs"]):
        raise Conflict(
            f"workflow '{workflow_key}' is at its concurrency limit",
            details={"limit": int(workflow["max_concurrent_runs"]), "in_flight": int(in_flight)},
        )

    row = session.execute(
        text(
            """
            INSERT INTO workflow_runs (tenant_id, workflow_id, workflow_version, run_id,
                                       parent_workflow_run_id, correlation_id, idempotency_key,
                                       status, input, deadline_at, started_at)
            VALUES (:t, :w, :v, :run, :parent, :corr, :idem, 'PENDING',
                    CAST(:input AS jsonb), :deadline, now())
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id,
            "w": workflow["id"],
            "v": workflow["current_version"],
            "run": run_id or (ctx.run_id or None),
            "parent": parent_workflow_run_id,
            "corr": ctx.correlation_id,
            "idem": idempotency_key,
            "input": json.dumps(payload, default=str),
            "deadline": utcnow() + timedelta(seconds=deadline_seconds),
        },
    ).one()
    workflow_run_id = str(row.id)

    publish(
        session,
        ctx,
        Event(
            event_type="Workflow.Started",
            aggregate_type="workflow_run",
            aggregate_id=workflow_run_id,
            payload={"workflow_key": workflow_key, "input_keys": sorted(payload)},
        ),
    )
    return workflow_run_id


def _load_definition(session: Session, tenant_id: str, run: dict) -> dict[str, Any]:
    row = session.execute(
        text(
            "SELECT definition FROM workflow_versions "
            "WHERE tenant_id = :t AND workflow_id = :w AND version = :v"
        ),
        {"t": tenant_id, "w": run["workflow_id"], "v": run["workflow_version"]},
    ).scalar_one()
    return row if isinstance(row, dict) else json.loads(row)


def claim_due_runs(session: Session, worker_id: str, *, tenant_id: str, limit: int = 10) -> list[str]:
    """Lease runs that are ready to advance, within one tenant.

    Deliberately tenant-scoped. Row level security has no bypass predicate, so
    a worker cannot sweep every tenant in one query — it iterates tenants and
    binds each, which is also what keeps one noisy tenant from starving the
    others in a single scan.
    """
    rows = session.execute(
        text(
            """
            UPDATE workflow_runs
               SET lease_owner = :worker,
                   lease_expires_at = now() + make_interval(secs => :lease),
                   status = CASE WHEN status = 'PENDING' THEN 'RUNNING' ELSE status END,
                   updated_at = now()
             WHERE id IN (
               SELECT id FROM workflow_runs
                WHERE tenant_id = CAST(:tenant AS uuid)
                  AND status IN ('PENDING', 'RUNNING')
                  AND paused = false
                  AND next_poll_at <= now()
                  AND (lease_expires_at IS NULL OR lease_expires_at < now())
                ORDER BY next_poll_at
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
             )
            RETURNING id
            """
        ),
        {"worker": worker_id, "lease": LEASE_SECONDS, "limit": limit, "tenant": tenant_id},
    ).all()
    return [str(r.id) for r in rows]


def advance(
    session: Session, ctx: ExecutionContext, workflow_run_id: str, *, worker_id: str = "inline"
) -> WorkflowRunState:
    """Advance one workflow run by one step."""
    run = (
        session.execute(
            text("SELECT * FROM workflow_runs WHERE tenant_id = :t AND id = CAST(:i AS uuid)"),
            {"t": ctx.tenant_id, "i": workflow_run_id},
        )
        .mappings()
        .first()
    )
    if run is None:
        raise NotFound(f"workflow run {workflow_run_id} not found")

    state = run["state"] if isinstance(run["state"], dict) else json.loads(run["state"] or "{}")
    if run["status"] in ("SUCCEEDED", "FAILED", "CANCELLED", "COMPENSATED", "TIMED_OUT"):
        return WorkflowRunState(workflow_run_id, str(run["status"]), int(run["current_step"]), state)

    if run["cancel_requested"]:
        return _finish(session, ctx, run, "CANCELLED", state, error_message="cancelled by operator")

    if run["deadline_at"] is not None and run["deadline_at"] <= utcnow():
        return _compensate_and_fail(session, ctx, run, state, "TIMEOUT", "workflow exceeded its deadline")

    definition = _load_definition(session, ctx.tenant_id, run)
    steps = definition["steps"]
    index = int(run["current_step"])

    if index >= len(steps):
        return _finish(session, ctx, run, "SUCCEEDED", state, output=state.get("_output", state))

    step = steps[index]
    workflow_ctx = ctx.with_workflow(
        WorkflowIdentity(
            workflow_id=str(run["workflow_id"]),
            workflow_run_id=workflow_run_id,
            workflow_version=str(run["workflow_version"]),
        )
    )

    idempotency_key = f"{workflow_run_id}:{index}:{step['key']}"
    existing = (
        session.execute(
            text(
                "SELECT id, status, output, attempt FROM workflow_steps "
                "WHERE workflow_run_id = CAST(:w AS uuid) AND step_index = :i"
            ),
            {"w": workflow_run_id, "i": index},
        )
        .mappings()
        .first()
    )

    if existing is not None and existing["status"] == "SUCCEEDED":
        # Already done — never re-execute a completed step.
        state[step["key"]] = existing["output"]
        return _next_step(session, ctx, run, state, index + 1)

    if existing is None:
        step_row = session.execute(
            text(
                """
                INSERT INTO workflow_steps (tenant_id, workflow_run_id, step_index, step_key,
                                            step_type, status, max_attempts, backoff_seconds,
                                            timeout_seconds, idempotency_key, input,
                                            compensation_for, started_at)
                VALUES (:t, :w, :i, :k, :type, 'RUNNING', :max_attempts, :backoff, :timeout,
                        :idem, CAST(:input AS jsonb), :comp, now())
                RETURNING id
                """
            ),
            {
                "t": ctx.tenant_id,
                "w": workflow_run_id,
                "i": index,
                "k": step["key"],
                "type": step["type"],
                "max_attempts": int(step.get("max_attempts", DEFAULT_MAX_ATTEMPTS)),
                "backoff": int(step.get("backoff_seconds", 2)),
                "timeout": int(step.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
                "idem": idempotency_key,
                "input": json.dumps(step.get("input", {}), default=str),
                "comp": None,
            },
        ).one()
        step_id = str(step_row.id)
        attempt = 0
    else:
        step_id = str(existing["id"])
        attempt = int(existing["attempt"])

    attempt += 1
    session.execute(
        text("UPDATE workflow_steps SET attempt = :a, status = 'RUNNING' WHERE id = :i"),
        {"a": attempt, "i": step_id},
    )

    handler = _STEP_HANDLERS.get(step["type"])
    if handler is None:
        return _compensate_and_fail(
            session,
            ctx,
            run,
            state,
            "CONFIGURATION",
            f"no handler registered for step type '{step['type']}'",
        )

    try:
        output = handler(session, workflow_ctx, step, state)
    except _WorkflowPaused as pause:
        session.execute(
            text(
                "UPDATE workflow_runs SET paused = true, state = CAST(:s AS jsonb), "
                "lease_owner = NULL, lease_expires_at = NULL, updated_at = now() WHERE id = :i"
            ),
            {"s": json.dumps({**state, **pause.state}, default=str), "i": workflow_run_id},
        )
        session.execute(
            text("UPDATE workflow_steps SET status = 'AWAITING_APPROVAL' WHERE id = :i"),
            {"i": step_id},
        )
        return WorkflowRunState(workflow_run_id, "AWAITING_APPROVAL", index, state)
    except AgenticError as exc:
        return _handle_step_failure(
            session,
            ctx,
            run,
            state,
            step,
            step_id,
            attempt,
            exc.error_class.value,
            exc.message,
            retryable=exc.retryable,
        )
    except Exception as exc:  # noqa: BLE001
        return _handle_step_failure(
            session, ctx, run, state, step, step_id, attempt, "INTERNAL", str(exc), retryable=False
        )

    session.execute(
        text(
            "UPDATE workflow_steps SET status = 'SUCCEEDED', output = CAST(:o AS jsonb), "
            "completed_at = now() WHERE id = :i"
        ),
        {"o": json.dumps(output, default=str), "i": step_id},
    )
    state[step["key"]] = output
    return _next_step(session, ctx, run, state, index + 1)


class _WorkflowPaused(Exception):
    """Raised by a step handler that parks the run awaiting an external event."""

    def __init__(self, state: dict[str, Any]) -> None:
        super().__init__("workflow paused")
        self.state = state


def pause_for_approval(approval_id: str) -> _WorkflowPaused:
    return _WorkflowPaused({"_awaiting_approval_id": approval_id})


def _next_step(
    session: Session, ctx: ExecutionContext, run: dict, state: dict, next_index: int
) -> WorkflowRunState:
    session.execute(
        text(
            "UPDATE workflow_runs SET current_step = :i, state = CAST(:s AS jsonb), "
            "next_poll_at = now(), lease_owner = NULL, lease_expires_at = NULL, "
            "updated_at = now() WHERE id = :id"
        ),
        {"i": next_index, "s": json.dumps(state, default=str), "id": run["id"]},
    )
    return WorkflowRunState(str(run["id"]), "RUNNING", next_index, state)


def _handle_step_failure(
    session: Session,
    ctx: ExecutionContext,
    run: dict,
    state: dict,
    step: dict,
    step_id: str,
    attempt: int,
    error_class: str,
    message: str,
    *,
    retryable: bool,
) -> WorkflowRunState:
    max_attempts = int(step.get("max_attempts", DEFAULT_MAX_ATTEMPTS))
    session.execute(
        text(
            "UPDATE workflow_steps SET status = 'FAILED', error_class = :c, error_message = :m WHERE id = :i"
        ),
        {"c": error_class, "m": message[:2000], "i": step_id},
    )

    if retryable and attempt < max_attempts:
        backoff = int(step.get("backoff_seconds", 2)) * (2 ** (attempt - 1))
        session.execute(
            text(
                "UPDATE workflow_runs SET next_poll_at = now() + make_interval(secs => :b), "
                "lease_owner = NULL, lease_expires_at = NULL, updated_at = now() WHERE id = :i"
            ),
            {"b": backoff, "i": run["id"]},
        )
        session.execute(text("UPDATE workflow_steps SET status = 'PENDING' WHERE id = :i"), {"i": step_id})
        return WorkflowRunState(str(run["id"]), "RUNNING", int(run["current_step"]), state)

    session.execute(
        text(
            """
            INSERT INTO workflow_dead_letters (tenant_id, workflow_run_id, workflow_step_id,
                                               reason, error_class, payload, attempts)
            VALUES (:t, :w, :s, :reason, :ec, CAST(:p AS jsonb), :a)
            """
        ),
        {
            "t": ctx.tenant_id,
            "w": run["id"],
            "s": step_id,
            "reason": f"step '{step['key']}' failed after {attempt} attempt(s)",
            "ec": error_class,
            "p": json.dumps({"message": message}, default=str),
            "a": attempt,
        },
    )
    return _compensate_and_fail(session, ctx, run, state, error_class, message)


def _compensate_and_fail(
    session: Session,
    ctx: ExecutionContext,
    run: dict,
    state: dict,
    error_class: str,
    message: str,
) -> WorkflowRunState:
    """Undo completed steps newest-first, then mark the run failed."""
    definition = _load_definition(session, ctx.tenant_id, run)
    compensated: list[str] = []

    completed = (
        session.execute(
            text(
                "SELECT id, step_index, step_key FROM workflow_steps "
                "WHERE workflow_run_id = :w AND status = 'SUCCEEDED' AND compensated = false "
                "ORDER BY step_index DESC"
            ),
            {"w": run["id"]},
        )
        .mappings()
        .all()
    )

    steps_by_key = {s["key"]: s for s in definition["steps"]}
    for row in completed:
        definition_step = steps_by_key.get(row["step_key"], {})
        compensation = definition_step.get("compensation")
        if not compensation:
            continue
        handler = _STEP_HANDLERS.get(compensation.get("type", ""))
        if handler is None:
            continue
        try:
            handler(session, ctx, compensation, state)
            compensated.append(row["step_key"])
            session.execute(
                text("UPDATE workflow_steps SET compensated = true WHERE id = :i"), {"i": row["id"]}
            )
        except Exception as exc:  # noqa: BLE001 - compensation is best-effort, always recorded
            session.execute(
                text(
                    """
                    INSERT INTO workflow_dead_letters (tenant_id, workflow_run_id, reason,
                                                       error_class, payload)
                    VALUES (:t, :w, :r, 'COMPENSATION_FAILED', CAST(:p AS jsonb))
                    """
                ),
                {
                    "t": ctx.tenant_id,
                    "w": run["id"],
                    "r": f"compensation for '{row['step_key']}' failed",
                    "p": json.dumps({"error": str(exc)}, default=str),
                },
            )

    status = "COMPENSATED" if compensated else "FAILED"
    state["_compensated_steps"] = compensated
    return _finish(session, ctx, run, status, state, error_class=error_class, error_message=message)


def _finish(
    session: Session,
    ctx: ExecutionContext,
    run: dict,
    status: str,
    state: dict,
    *,
    output: dict | None = None,
    error_class: str = "",
    error_message: str = "",
) -> WorkflowRunState:
    session.execute(
        text(
            """
            UPDATE workflow_runs
               SET status = CAST(:s AS run_status), state = CAST(:state AS jsonb),
                   output = CAST(:output AS jsonb), error_class = :ec, error_message = :em,
                   completed_at = now(), lease_owner = NULL, lease_expires_at = NULL,
                   updated_at = now()
             WHERE id = :i
            """
        ),
        {
            "s": status,
            "state": json.dumps(state, default=str),
            "output": json.dumps(output, default=str) if output is not None else None,
            "ec": error_class,
            "em": error_message[:2000],
            "i": run["id"],
        },
    )
    publish(
        session,
        ctx,
        Event(
            event_type="Workflow.Completed" if status == "SUCCEEDED" else "Workflow.Failed",
            aggregate_type="workflow_run",
            aggregate_id=str(run["id"]),
            payload={"status": status, "error_class": error_class, "error": error_message[:500]},
        ),
    )
    return WorkflowRunState(
        str(run["id"]), status, int(run["current_step"]), state, output, error_class, error_message
    )


def run_to_completion(
    session: Session, ctx: ExecutionContext, workflow_run_id: str, *, max_iterations: int = 200
) -> WorkflowRunState:
    """Drive a run inline until it finishes, pauses or hits the iteration cap.

    Used by tests and by the synchronous API path. The worker uses
    :func:`claim_due_runs` plus :func:`advance` instead.
    """
    state = WorkflowRunState(workflow_run_id, "PENDING", 0, {})
    for _ in range(max_iterations):
        state = advance(session, ctx, workflow_run_id)
        if state.status in (
            "SUCCEEDED",
            "FAILED",
            "CANCELLED",
            "COMPENSATED",
            "TIMED_OUT",
            "AWAITING_APPROVAL",
        ):
            return state
    raise Conflict(
        "workflow did not settle within the iteration limit",
        details={"workflow_run_id": workflow_run_id, "max_iterations": max_iterations},
    )


def cancel(session: Session, ctx: ExecutionContext, workflow_run_id: str, reason: str = "") -> bool:
    result = session.execute(
        text(
            "UPDATE workflow_runs SET cancel_requested = true, next_poll_at = now(), "
            "updated_at = now() WHERE tenant_id = :t AND id = CAST(:i AS uuid) "
            "AND status IN ('PENDING', 'RUNNING')"
        ),
        {"t": ctx.tenant_id, "i": workflow_run_id},
    )
    return result.rowcount > 0


def resume(session: Session, ctx: ExecutionContext, workflow_run_id: str) -> bool:
    result = session.execute(
        text(
            "UPDATE workflow_runs SET paused = false, next_poll_at = now(), updated_at = now() "
            "WHERE tenant_id = :t AND id = CAST(:i AS uuid) AND paused = true"
        ),
        {"t": ctx.tenant_id, "i": workflow_run_id},
    )
    return result.rowcount > 0
