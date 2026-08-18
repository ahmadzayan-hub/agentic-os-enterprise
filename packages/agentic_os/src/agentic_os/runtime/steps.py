"""Standard workflow step handlers.

Importing this module registers the step types a workflow definition may use.
Each handler receives the workflow's accumulated state and returns the output
recorded for its step, which becomes available to later steps under its key.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.control.approval_engine import ApprovalCard, request_approval
from agentic_os.core.context import ExecutionContext
from agentic_os.core.errors import Conflict, NotFound, ValidationError
from agentic_os.runtime.workflow_engine import pause_for_approval, register_step_type


def _resolve(value: Any, state: dict[str, Any]) -> Any:
    """Resolve ``$step_key.field`` references against accumulated state."""
    if isinstance(value, str) and value.startswith("$"):
        path = value[1:].split(".")
        current: Any = state
        for part in path:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current
    if isinstance(value, dict):
        return {k: _resolve(v, state) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, state) for v in value]
    return value


@register_step_type("SKILL")
def skill_step(
    session: Session, ctx: ExecutionContext, step: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Execute a skill under the named agent's contract."""
    from agentic_os.runtime.agent_runtime import AgentRuntime

    agent_key = step.get("agent")
    if not agent_key:
        raise ValidationError(f"step '{step['key']}' does not name an agent")

    runtime = AgentRuntime(session)
    agent = runtime.open(ctx, agent_key)
    params = _resolve(step.get("input", {}), state)
    result = runtime.run_skill(
        ctx,
        agent,
        step["skill"],
        params,
        idempotency_key=f"{ctx.workflow.workflow_run_id if ctx.workflow else 'inline'}:{step['key']}",
    )
    return {
        **result.output,
        "_deterministic": result.deterministic,
        "_cost_usd": result.cost_usd,
        "_citations": result.citations,
    }


@register_step_type("TOOL")
def tool_step(
    session: Session, ctx: ExecutionContext, step: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Invoke a tool through the gateway under the named agent's contract."""
    from agentic_os.runtime.agent_runtime import AgentRuntime

    agent_key = step.get("agent")
    if not agent_key:
        raise ValidationError(f"step '{step['key']}' does not name an agent")

    runtime = AgentRuntime(session)
    agent = runtime.open(ctx, agent_key)
    params = _resolve(step.get("input", {}), state)
    run_key = ctx.workflow.workflow_run_id if ctx.workflow else "inline"
    result = runtime.invoke_tool(
        ctx,
        agent,
        step["tool"],
        params,
        idempotency_key=f"{run_key}:{step['key']}",
        approval_id=str(state.get("_approval_id", "")),
    )
    return {
        "decision": result.decision,
        "verification": result.verification,
        **(result.result or {}),
    }


@register_step_type("APPROVAL")
def approval_step(
    session: Session, ctx: ExecutionContext, step: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Park the workflow until a human decides.

    On first execution this raises a pause, which releases the worker lease and
    leaves the run consuming nothing. On resume it reads the decision and either
    continues or fails the run.
    """
    awaiting = state.get("_awaiting_approval_id")
    if awaiting:
        row = session.execute(
            text("SELECT status FROM approvals WHERE tenant_id = :t AND id = CAST(:i AS uuid)"),
            {"t": ctx.tenant_id, "i": awaiting},
        ).mappings().first()
        if row is None:
            raise NotFound(f"approval {awaiting} disappeared")
        if row["status"] == "PENDING":
            raise pause_for_approval(awaiting)
        if row["status"] != "APPROVED":
            raise Conflict(
                f"approval was {row['status']}", details={"approval_id": awaiting}
            )
        state["_approval_id"] = awaiting
        return {"approval_id": awaiting, "status": row["status"]}

    config = step.get("input", {})
    approval_id = request_approval(
        session,
        ctx,
        ApprovalCard(
            action=str(config.get("action", step["key"])),
            target=str(config.get("target", "")),
            proposing_agent=str(step.get("agent", "")),
            autonomy_level=str(config.get("autonomy_level", "A4")),
            risk_class=str(config.get("risk_class", "HIGH")),
            financial_impact_usd=float(config.get("financial_impact_usd", 0)),
            reversibility=str(config.get("reversibility", "IRREVERSIBLE")),
            confidence=config.get("confidence"),
            reason=str(config.get("reason", "workflow step requires human authorisation")),
            consequences=str(
                config.get("consequences", "the following workflow steps will execute")
            ),
            evidence=list(config.get("evidence", [])) or [{"workflow_step": step["key"]}],
            sources=list(config.get("sources", [])),
        ),
        mode=str(config.get("mode", "SINGLE")),
        required_approvals=int(config.get("required_approvals", 1)),
        approver_roles=list(config.get("approver_roles", ["approver"])),
        run_id=ctx.run_id or None,
    )
    raise pause_for_approval(approval_id)


@register_step_type("EVENT")
def event_step(
    session: Session, ctx: ExecutionContext, step: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Publish a domain event through the transactional outbox."""
    from agentic_os.runtime.events import Event, publish

    config = step.get("input", {})
    event_id = publish(
        session,
        ctx,
        Event(
            event_type=str(config.get("event_type", "Workflow.Completed")),
            payload=_resolve(config.get("payload", {}), state),
            aggregate_type=str(config.get("aggregate_type", "workflow_run")),
            aggregate_id=ctx.workflow.workflow_run_id if ctx.workflow else "",
        ),
    )
    return {"event_id": event_id}


@register_step_type("TASK")
def task_step(
    session: Session, ctx: ExecutionContext, step: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Create an internal task. Used as a compensating action."""
    config = _resolve(step.get("input", {}), state)
    row = session.execute(
        text(
            """
            INSERT INTO tasks (tenant_id, run_id, title, description, priority)
            VALUES (:t, :run, :title, :desc, :priority)
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id,
            "run": ctx.run_id or None,
            "title": str(config.get("title", step["key"]))[:300],
            "desc": str(config.get("description", "")),
            "priority": str(config.get("priority", "MEDIUM")),
        },
    ).one()
    return {"task_id": str(row.id)}


@register_step_type("NOOP")
def noop_step(
    session: Session, ctx: ExecutionContext, step: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Do nothing successfully. Used for tests and as a compensation placeholder."""
    return {"noop": True, "key": step["key"]}


@register_step_type("FAIL")
def fail_step(
    session: Session, ctx: ExecutionContext, step: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    """Fail deliberately. Used to exercise retry and compensation paths."""
    config = step.get("input", {})
    error_class = str(config.get("error_class", "INTERNAL"))
    message = str(config.get("message", "deliberate failure"))
    if error_class == "UPSTREAM_UNAVAILABLE":
        from agentic_os.core.errors import UpstreamUnavailable

        raise UpstreamUnavailable(message)
    raise ValidationError(message)
