"""Runs: submit an objective and inspect the full governed execution record."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from agentic_os.api.deps import CtxDep, DbDep, require_permission
from agentic_os.api.serialization import jsonify, row as json_row, rows as json_rows
from agentic_os.control.conductor import Conductor
from agentic_os.core.errors import AgenticError, NotFound

router = APIRouter(prefix="/runs", tags=["runs"])


class SubmitRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=4000)
    requested_autonomy: str = Field(default="A1", pattern="^A[0-4]$")
    idempotency_key: str | None = Field(default=None, max_length=128)
    dry_run: bool = False


@router.post("", dependencies=[Depends(require_permission("runs:create", resource_type="run"))])
def submit(payload: SubmitRequest, ctx: CtxDep, db: DbDep) -> dict:
    try:
        outcome = Conductor(db).submit(
            ctx,
            payload.objective,
            requested_autonomy=payload.requested_autonomy,
            idempotency_key=payload.idempotency_key,
            dry_run=payload.dry_run,
        )
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    return jsonify(outcome.to_dict())


@router.get("", dependencies=[Depends(require_permission("runs:read", resource_type="run"))])
def list_runs(
    ctx: CtxDep,
    db: DbDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    rows = db.execute(
        text(
            """
            SELECT r.id, r.objective, r.status, r.owner_agent_key, r.autonomy_level,
                   r.risk_class, r.risk_score, r.confidence, r.classification, r.cost_usd,
                   r.input_tokens, r.output_tokens, r.tool_call_count, r.duration_ms,
                   r.error_class, r.created_at, r.completed_at,
                   u.email AS requested_by_email,
                   (SELECT count(*) FROM run_steps s WHERE s.run_id = r.id) AS step_count,
                   (SELECT count(*) FROM approvals a
                     WHERE a.run_id = r.id AND a.status = 'PENDING') AS pending_approvals
            FROM runs r
            LEFT JOIN users u ON u.id = r.requested_by
            WHERE r.tenant_id = :t
              AND (CAST(:status AS text) IS NULL OR r.status::text = :status)
            ORDER BY r.created_at DESC
            LIMIT :limit
            """
        ),
        {"t": ctx.tenant_id, "status": status_filter, "limit": limit},
    ).mappings()
    return {"runs": json_rows(rows)}


@router.get(
    "/{run_id}", dependencies=[Depends(require_permission("runs:read", resource_type="run"))]
)
def run_detail(run_id: str, ctx: CtxDep, db: DbDep) -> dict:
    """The complete governed record for one run."""
    run = db.execute(
        text(
            """
            SELECT r.*, u.email AS requested_by_email
            FROM runs r LEFT JOIN users u ON u.id = r.requested_by
            WHERE r.tenant_id = :t AND r.id = CAST(:i AS uuid)
            """
        ),
        {"t": ctx.tenant_id, "i": run_id},
    ).mappings().first()
    if run is None:
        raise HTTPException(status_code=404, detail=NotFound(f"run {run_id} not found").to_dict())

    def rows(sql: str) -> list[dict]:
        return json_rows(db.execute(text(sql), {"t": ctx.tenant_id, "i": run_id}).mappings())

    return {
        "run": json_row(run),
        "plan": rows(
            "SELECT version, planner, steps, plan_hash, validated, validation_errors, "
            "rationale, estimated_cost_usd FROM plans "
            "WHERE tenant_id = :t AND run_id = CAST(:i AS uuid) ORDER BY version DESC"
        ),
        "steps": rows(
            "SELECT step_index, step_key, step_type, agent_key, skill_key, tool_key, status, "
            "attempt, output, error_class, error_message, cost_usd, input_tokens, "
            "output_tokens, latency_ms, started_at, completed_at FROM run_steps "
            "WHERE tenant_id = :t AND run_id = CAST(:i AS uuid) ORDER BY step_index"
        ),
        "policy_decisions": rows(
            "SELECT action, resource, effect, reason, matched_policies, obligations, evaluated_at "
            "FROM policy_decisions WHERE tenant_id = :t AND run_id = CAST(:i AS uuid) "
            "ORDER BY evaluated_at"
        ),
        "risk_assessments": rows(
            "SELECT action, risk_class, risk_score, factors, reversibility, "
            "financial_impact_usd, required_autonomy, assessed_at FROM risk_assessments "
            "WHERE tenant_id = :t AND run_id = CAST(:i AS uuid) ORDER BY assessed_at"
        ),
        "tool_calls": rows(
            "SELECT tool_key, agent_key, gateway_decision, denial_stage, denial_reason, "
            "verification_status, latency_ms, parameters_redacted, created_at FROM tool_calls "
            "WHERE tenant_id = :t AND run_id = CAST(:i AS uuid) ORDER BY created_at"
        ),
        "approvals": rows(
            "SELECT id, action, target, status, mode, required_approvals, risk_class, "
            "autonomy_level, financial_impact_usd, reversibility, reason, consequences, "
            "expires_at, created_at FROM approvals "
            "WHERE tenant_id = :t AND run_id = CAST(:i AS uuid) ORDER BY created_at"
        ),
        "citations": rows(
            "SELECT c.chunk_id, c.document_id, c.verified, d.title, ch.section_path, "
            "left(ch.content, 400) AS snippet FROM citations c "
            "LEFT JOIN chunks ch ON ch.id = c.chunk_id "
            "LEFT JOIN documents d ON d.id = ch.document_id "
            "WHERE c.tenant_id = :t AND c.run_id = CAST(:i AS uuid)"
        ),
        "model_calls": rows(
            "SELECT provider, model_key, agent_key, input_tokens, output_tokens, cost_usd, "
            "occurred_at FROM cost_records "
            "WHERE tenant_id = :t AND run_id = CAST(:i AS uuid) AND category = 'MODEL' "
            "ORDER BY occurred_at"
        ),
        "trace": rows(
            "SELECT trace_id, span_id, parent_span_id, name, kind, status, duration_ms, "
            "started_at FROM traces WHERE tenant_id = :t AND run_id = CAST(:i AS uuid) "
            "ORDER BY started_at"
        ),
        "audit": rows(
            "SELECT sequence_no, category, action, outcome, resource_type, resource_id, "
            "occurred_at FROM audit_events WHERE tenant_id = :t AND run_id = CAST(:i AS uuid) "
            "ORDER BY sequence_no"
        ),
    }


@router.post(
    "/{run_id}/cancel",
    dependencies=[Depends(require_permission("runs:cancel", resource_type="run"))],
)
def cancel_run(run_id: str, ctx: CtxDep, db: DbDep) -> dict:
    result = db.execute(
        text(
            "UPDATE runs SET status = 'CANCELLED', completed_at = now(), updated_at = now() "
            "WHERE tenant_id = :t AND id = CAST(:i AS uuid) "
            "AND status IN ('PENDING', 'PLANNING', 'RUNNING', 'AWAITING_APPROVAL')"
        ),
        {"t": ctx.tenant_id, "i": run_id},
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=409,
            detail={"error": "CONFLICT", "message": "run is not in a cancellable state"},
        )
    return {"run_id": run_id, "status": "CANCELLED"}
