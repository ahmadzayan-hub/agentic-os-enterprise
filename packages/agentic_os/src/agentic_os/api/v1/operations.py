"""Operational surfaces: command centre, analytics, costs, outcomes, workflows."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from agentic_os.api.deps import CtxDep, DbDep, require_permission
from agentic_os.api.serialization import jsonify, row as json_row, rows as json_rows
from agentic_os.core.errors import AgenticError
from agentic_os.observability import telemetry
from agentic_os.outcomes import engine as outcomes_engine
from agentic_os.runtime import events as event_bus
from agentic_os.runtime import workflow_engine

router = APIRouter(tags=["operations"])


@router.get(
    "/command-center",
    dependencies=[Depends(require_permission("analytics:read", resource_type="dashboard"))],
)
def command_center(ctx: CtxDep, db: DbDep) -> dict:
    """Everything the Home surface needs, in one round trip.

    Organised around attention and decisions rather than around tables.
    """
    from agentic_os.control import approval_engine

    approval_engine.expire_due_approvals(db, ctx.tenant_id)

    attention = {
        "pending_approvals": approval_engine.pending_for_principal(db, ctx, limit=10),
        "failed_runs": json_rows(db.execute(
                text(
                    "SELECT id, objective, error_class, error_message, completed_at FROM runs "
                    "WHERE tenant_id = :t AND status = 'FAILED' "
                    "ORDER BY completed_at DESC NULLS LAST LIMIT 5"
                ),
                {"t": ctx.tenant_id},
            ).mappings()),
        "security_findings": json_rows(db.execute(
                text(
                    "SELECT finding_type, severity, source, created_at FROM security_findings "
                    "WHERE tenant_id = :t AND severity IN ('HIGH', 'CRITICAL') "
                    "ORDER BY created_at DESC LIMIT 5"
                ),
                {"t": ctx.tenant_id},
            ).mappings()),
        "open_incidents": json_rows(db.execute(
                text(
                    "SELECT incident_key, title, severity, status, detected_at FROM incidents "
                    "WHERE tenant_id = :t AND status NOT IN ('RESOLVED', 'CLOSED') "
                    "ORDER BY detected_at DESC LIMIT 5"
                ),
                {"t": ctx.tenant_id},
            ).mappings()),
        "dead_letters": len(event_bus.dead_letters(db, ctx.tenant_id, limit=50)),
        "expired_evidence": int(
            db.execute(
                text(
                    "SELECT count(*) FROM evidence WHERE tenant_id = :t AND status = 'EXPIRED'"
                ),
                {"t": ctx.tenant_id},
            ).scalar_one()
        ),
    }

    kill_switches = json_rows(db.execute(
            text(
                "SELECT scope, target_key, engaged, reason FROM kill_switches "
                "WHERE (tenant_id = :t OR tenant_id IS NULL) AND engaged ORDER BY scope"
            ),
            {"t": ctx.tenant_id},
        ).mappings())

    return jsonify(
        {
        "requires_attention": attention,
        "agent_operations": telemetry.platform_metrics(db, ctx.tenant_id, window_hours=24),
        "business_pulse": outcomes_engine.operational_summary(db, ctx.tenant_id, window_days=7),
        "engaged_kill_switches": kill_switches,
        "read_only_mode": any(k["scope"] == "READ_ONLY" for k in kill_switches),
        }
    )


@router.get(
    "/analytics",
    dependencies=[Depends(require_permission("analytics:read", resource_type="dashboard"))],
)
def analytics(
    ctx: CtxDep, db: DbDep, window_hours: Annotated[int, Query(ge=1, le=8760)] = 168
) -> dict:
    return jsonify(telemetry.platform_metrics(db, ctx.tenant_id, window_hours=window_hours))


@router.get(
    "/outcomes", dependencies=[Depends(require_permission("outcomes:read", resource_type="outcome"))]
)
def outcomes(
    ctx: CtxDep, db: DbDep, window_days: Annotated[int, Query(ge=1, le=365)] = 30
) -> dict:
    return jsonify(outcomes_engine.roi_summary(db, ctx.tenant_id, window_days=window_days))


@router.get("/costs", dependencies=[Depends(require_permission("costs:read", resource_type="cost"))])
def costs(ctx: CtxDep, db: DbDep, window_days: Annotated[int, Query(ge=1, le=365)] = 30) -> dict:
    by_model = db.execute(
        text(
            "SELECT model_key, provider, count(*) AS calls, "
            "sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens, "
            "sum(cost_usd) AS cost_usd FROM cost_records "
            "WHERE tenant_id = :t AND category = 'MODEL' "
            "AND occurred_at >= now() - make_interval(days => :d) "
            "GROUP BY model_key, provider ORDER BY cost_usd DESC"
        ),
        {"t": ctx.tenant_id, "d": window_days},
    ).mappings()
    by_agent = db.execute(
        text(
            "SELECT agent_key, count(*) AS calls, sum(cost_usd) AS cost_usd FROM cost_records "
            "WHERE tenant_id = :t AND occurred_at >= now() - make_interval(days => :d) "
            "AND agent_key <> '' GROUP BY agent_key ORDER BY cost_usd DESC"
        ),
        {"t": ctx.tenant_id, "d": window_days},
    ).mappings()
    budgets = db.execute(
        text(
            "SELECT scope, scope_key, period, cost_cap_usd, token_cap, alert_at_pct, hard_stop, "
            "fallback_model_key FROM budgets WHERE tenant_id = :t ORDER BY scope"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    spend_today = float(
        db.execute(
            text(
                "SELECT COALESCE(sum(cost_usd), 0) FROM cost_records "
                "WHERE tenant_id = :t AND occurred_at >= date_trunc('day', now())"
            ),
            {"t": ctx.tenant_id},
        ).scalar_one()
    )
    return {
        "window_days": window_days,
        "spend_today_usd": round(spend_today, 6),
        "by_model": json_rows(by_model),
        "by_agent": json_rows(by_agent),
        "budgets": json_rows(budgets),
    }


# ------------------------------------------------------------------- workflows
@router.get(
    "/workflows",
    dependencies=[Depends(require_permission("workflows:read", resource_type="workflow"))],
)
def list_workflows(ctx: CtxDep, db: DbDep) -> dict:
    rows = db.execute(
        text(
            "SELECT w.workflow_key, w.name, w.description, w.owner_team, w.status, "
            "w.current_version, w.max_concurrent_runs, wv.definition, wv.definition_hash, "
            "(SELECT count(*) FROM workflow_runs wr WHERE wr.workflow_id = w.id) AS run_count "
            "FROM workflows w LEFT JOIN workflow_versions wv "
            "  ON wv.workflow_id = w.id AND wv.version = w.current_version "
            "WHERE w.tenant_id = :t ORDER BY w.workflow_key"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"workflows": json_rows(rows), "step_types": sorted(
        workflow_engine.registered_step_types()
    )}


@router.get(
    "/workflows/runs",
    dependencies=[Depends(require_permission("workflows:read", resource_type="workflow"))],
)
def list_workflow_runs(ctx: CtxDep, db: DbDep, limit: Annotated[int, Query(le=200)] = 50) -> dict:
    rows = db.execute(
        text(
            "SELECT wr.id, w.workflow_key, wr.status, wr.current_step, wr.paused, "
            "wr.error_class, wr.error_message, wr.started_at, wr.completed_at "
            "FROM workflow_runs wr JOIN workflows w ON w.id = wr.workflow_id "
            "WHERE wr.tenant_id = :t ORDER BY wr.created_at DESC LIMIT :l"
        ),
        {"t": ctx.tenant_id, "l": limit},
    ).mappings()
    return {"workflow_runs": json_rows(rows)}


class StartWorkflowRequest(BaseModel):
    workflow_key: str = Field(min_length=2, max_length=64)
    input: dict = Field(default_factory=dict)
    idempotency_key: str | None = None


@router.post(
    "/workflows/runs",
    dependencies=[Depends(require_permission("workflows:execute", resource_type="workflow"))],
)
def start_workflow(payload: StartWorkflowRequest, ctx: CtxDep, db: DbDep) -> dict:
    import agentic_os.runtime.steps  # noqa: F401 - register step handlers

    try:
        run_id = workflow_engine.start(
            db, ctx, payload.workflow_key, payload.input,
            idempotency_key=payload.idempotency_key,
        )
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    return {"workflow_run_id": run_id, "status": "PENDING"}


# ------------------------------------------------------------------- incidents
@router.get(
    "/incidents",
    dependencies=[Depends(require_permission("incidents:read", resource_type="incident"))],
)
def list_incidents(ctx: CtxDep, db: DbDep) -> dict:
    rows = db.execute(
        text(
            "SELECT incident_key, title, description, severity, status, category, root_cause, "
            "detected_at, resolved_at FROM incidents WHERE tenant_id = :t "
            "ORDER BY detected_at DESC LIMIT 100"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    alerts = db.execute(
        text(
            "SELECT alert_type, severity, title, detail, source, acknowledged_at, created_at "
            "FROM alerts WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 100"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"incidents": json_rows(rows), "alerts": json_rows(alerts)}


# ------------------------------------------------------------------ organization
@router.get("/organization", dependencies=[Depends(require_permission("org:read"))])
def organization(ctx: CtxDep, db: DbDep) -> dict:
    tenant = db.execute(
        text(
            "SELECT t.slug, t.name, t.region, t.data_residency, t.default_classification, "
            "t.retention_days, t.daily_cost_cap_usd, t.status, o.slug AS org_slug, "
            "o.name AS org_name FROM tenants t JOIN organizations o ON o.id = t.organization_id "
            "WHERE t.id = :t"
        ),
        {"t": ctx.tenant_id},
    ).mappings().first()
    users = db.execute(
        text(
            "SELECT u.email, u.display_name, u.clearance, u.status, u.mfa_enrolled, "
            "u.last_login_at, array_agg(r.slug ORDER BY r.slug) FILTER (WHERE r.slug IS NOT NULL) "
            "  AS roles "
            "FROM users u LEFT JOIN user_roles ur ON ur.user_id = u.id "
            "LEFT JOIN roles r ON r.id = ur.role_id "
            "WHERE u.tenant_id = :t AND u.deleted_at IS NULL "
            "GROUP BY u.id, u.email, u.display_name, u.clearance, u.status, u.mfa_enrolled, "
            "u.last_login_at ORDER BY u.email"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"tenant": json_row(tenant), "users": json_rows(users)}


# ------------------------------------------------------------------ resilience
@router.get(
    "/resilience",
    dependencies=[Depends(require_permission("incidents:read", resource_type="incident"))],
)
def resilience(ctx: CtxDep, db: DbDep) -> dict:
    """Backup and restore evidence.

    Disaster recovery is a platform-scope activity, so these rows carry no
    tenant id; the RLS policy on both tables admits platform rows to every
    tenant's read path while still refusing cross-tenant rows.
    """
    backups = db.execute(
        text(
            "SELECT backup_type, scope, artifact_hash, size_bytes, status, started_at, "
            "completed_at FROM backup_records WHERE tenant_id IS NULL OR tenant_id = :t "
            "ORDER BY started_at DESC LIMIT 20"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    restores = db.execute(
        text(
            "SELECT environment, outcome, rpo_achieved_seconds, rto_achieved_seconds, "
            "verified_rows, notes, executed_by, executed_at FROM restore_tests "
            "WHERE tenant_id IS NULL OR tenant_id = :t ORDER BY executed_at DESC LIMIT 20"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"backups": json_rows(backups), "restore_tests": json_rows(restores)}
