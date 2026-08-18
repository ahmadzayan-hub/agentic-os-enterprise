"""Governance surfaces: approvals, policies, risks, evidence, audit, security."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from agentic_os.api.deps import CtxDep, DbDep, require_permission
from agentic_os.api.serialization import jsonify, row as json_row, rows as json_rows
from agentic_os.assurance.audit import AuditLedger
from agentic_os.assurance import evidence as evidence_engine
from agentic_os.control import approval_engine
from agentic_os.core.errors import AgenticError

router = APIRouter(tags=["governance"])


# ------------------------------------------------------------------ approvals
class DecisionRequest(BaseModel):
    decision: str = Field(pattern="^(APPROVED|REJECTED|CHANGES_REQUESTED)$")
    comment: str = Field(default="", max_length=2000)


@router.get(
    "/approvals",
    dependencies=[Depends(require_permission("approvals:read", resource_type="approval"))],
)
def list_approvals(ctx: CtxDep, db: DbDep, mine: bool = True) -> dict:
    approval_engine.expire_due_approvals(db, ctx.tenant_id)
    if mine:
        return jsonify({"approvals": approval_engine.pending_for_principal(db, ctx)})
    rows = db.execute(
        text(
            "SELECT id, action, target, status, mode, required_approvals, risk_class, "
            "autonomy_level, financial_impact_usd, reversibility, confidence, reason, "
            "consequences, evidence, sources, policy_refs, requested_by_agent, expires_at, "
            "created_at FROM approvals WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 100"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"approvals": json_rows(rows)}


@router.get(
    "/approvals/{approval_id}",
    dependencies=[Depends(require_permission("approvals:read", resource_type="approval"))],
)
def get_approval(approval_id: str, ctx: CtxDep, db: DbDep) -> dict:
    try:
        return jsonify(approval_engine.get_approval(db, ctx.tenant_id, approval_id))
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc


@router.post(
    "/approvals/{approval_id}/decide",
    dependencies=[Depends(require_permission("approvals:decide", resource_type="approval"))],
)
def decide_approval(
    approval_id: str, payload: DecisionRequest, ctx: CtxDep, db: DbDep
) -> dict:
    if ctx.human is None or not ctx.human.mfa_satisfied:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "AUTHORIZATION",
                "message": "approval decisions require a satisfied second factor",
            },
        )
    try:
        return approval_engine.decide(
            db, ctx, approval_id, payload.decision, comment=payload.comment  # type: ignore[arg-type]
        )
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc


# ------------------------------------------------------------------- policies
@router.get(
    "/policies", dependencies=[Depends(require_permission("policies:read", resource_type="policy"))]
)
def list_policies(ctx: CtxDep, db: DbDep) -> dict:
    rows = db.execute(
        text(
            "SELECT p.policy_key, p.name, p.description, p.category, p.owner_team, "
            "p.enforcement, p.status, p.current_version, pv.rules, pv.rules_hash "
            "FROM policies p LEFT JOIN policy_versions pv "
            "  ON pv.policy_id = p.id AND pv.version = p.current_version "
            "WHERE p.tenant_id = :t ORDER BY p.policy_key"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"policies": json_rows(rows)}


@router.get(
    "/policies/decisions",
    dependencies=[Depends(require_permission("policies:read", resource_type="policy"))],
)
def policy_decisions(ctx: CtxDep, db: DbDep, limit: Annotated[int, Query(le=500)] = 100) -> dict:
    rows = db.execute(
        text(
            "SELECT action, resource, effect, reason, matched_policies, obligations, run_id, "
            "evaluated_at FROM policy_decisions WHERE tenant_id = :t "
            "ORDER BY evaluated_at DESC LIMIT :l"
        ),
        {"t": ctx.tenant_id, "l": limit},
    ).mappings()
    return {"decisions": json_rows(rows)}


# ----------------------------------------------------------------------- risks
@router.get(
    "/risks", dependencies=[Depends(require_permission("risks:read", resource_type="risk"))]
)
def list_risks(ctx: CtxDep, db: DbDep, limit: Annotated[int, Query(le=500)] = 100) -> dict:
    rows = db.execute(
        text(
            "SELECT action, risk_class, risk_score, factors, reversibility, "
            "financial_impact_usd, required_autonomy, run_id, assessed_at "
            "FROM risk_assessments WHERE tenant_id = :t ORDER BY assessed_at DESC LIMIT :l"
        ),
        {"t": ctx.tenant_id, "l": limit},
    ).mappings()
    return {"risk_assessments": json_rows(rows)}


# -------------------------------------------------------------------- evidence
@router.get(
    "/evidence", dependencies=[Depends(require_permission("evidence:read", resource_type="control"))]
)
def maturity(ctx: CtxDep, db: DbDep) -> dict:
    evidence_engine.apply_expiry(db, ctx.tenant_id)
    report = evidence_engine.latest_report(db, ctx.tenant_id)
    if report is None:
        return {
            "available": False,
            "message": (
                "no evidence has been collected for this tenant; run "
                "'agentic-evidence collect' to derive maturity from the test suite"
            ),
        }
    return jsonify({"available": True, **report})


@router.get(
    "/evidence/controls",
    dependencies=[Depends(require_permission("evidence:read", resource_type="control"))],
)
def list_controls(ctx: CtxDep, db: DbDep) -> dict:
    rows = db.execute(
        text(
            """
            SELECT DISTINCT ON (c.control_id)
                   c.control_id, c.domain, c.title, c.requirement, c.implementation, c.weight,
                   c.critical, c.applicable, c.standard_mappings, c.automated_test,
                   c.expected_result, c.owner_team, c.evidence_ttl_days,
                   e.status, e.actual_result, e.collected_at, e.expires_at, e.commit_sha
            FROM controls c
            LEFT JOIN evidence e ON e.control_id = c.control_id AND e.tenant_id = c.tenant_id
            WHERE c.tenant_id = :t
            ORDER BY c.control_id, e.collected_at DESC NULLS LAST
            """
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"controls": json_rows(rows)}


@router.get(
    "/evidence/certifications",
    dependencies=[Depends(require_permission("evidence:read", resource_type="control"))],
)
def certifications(ctx: CtxDep, db: DbDep) -> dict:
    rows = db.execute(
        text(
            "SELECT release_tag, commit_sha, environment, score, certified, critical_blockers, "
            "domain_scores, bundle_hash, created_at FROM certifications "
            "WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 50"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"certifications": json_rows(rows)}


# ----------------------------------------------------------------------- audit
@router.get(
    "/audit", dependencies=[Depends(require_permission("audit:read", resource_type="audit"))]
)
def audit_log(
    ctx: CtxDep,
    db: DbDep,
    category: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    return jsonify(
        {"events": AuditLedger(db).recent(ctx.tenant_id, limit=limit, category=category)}
    )


@router.get(
    "/audit/verify",
    dependencies=[Depends(require_permission("audit:verify", resource_type="audit"))],
)
def verify_audit_chain(ctx: CtxDep, db: DbDep) -> dict:
    return AuditLedger(db).verify_chain(ctx.tenant_id)


# -------------------------------------------------------------------- security
@router.get(
    "/security", dependencies=[Depends(require_permission("security:read", resource_type="security"))]
)
def security_posture(ctx: CtxDep, db: DbDep) -> dict:
    findings = db.execute(
        text(
            "SELECT finding_type, severity, source, detail, blocked, created_at "
            "FROM security_findings WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 100"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    switches = db.execute(
        text(
            "SELECT scope, target_key, engaged, reason, engaged_at FROM kill_switches "
            "WHERE tenant_id = :t OR tenant_id IS NULL ORDER BY scope, target_key"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    denials = db.execute(
        text(
            "SELECT denial_stage, count(*) AS n FROM tool_calls "
            "WHERE tenant_id = :t AND gateway_decision = 'DENIED' "
            "AND created_at >= now() - interval '7 days' GROUP BY denial_stage ORDER BY n DESC"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {
        "findings": json_rows(findings),
        "kill_switches": json_rows(switches),
        "denials_by_stage": json_rows(denials),
    }


class KillSwitchRequest(BaseModel):
    scope: str = Field(pattern="^(GLOBAL|TENANT|AGENT|MODEL|TOOL|CONNECTOR|WORKFLOW|READ_ONLY)$")
    target_key: str = Field(default="", max_length=128)
    engaged: bool
    reason: str = Field(min_length=5, max_length=500)


@router.post(
    "/security/kill-switch",
    dependencies=[Depends(require_permission("killswitch:engage", resource_type="security"))],
)
def set_kill_switch(payload: KillSwitchRequest, ctx: CtxDep, db: DbDep) -> dict:
    if ctx.human is None or not ctx.human.mfa_satisfied:
        raise HTTPException(
            status_code=403,
            detail={"error": "AUTHORIZATION", "message": "kill switches require a second factor"},
        )
    db.execute(
        text(
            """
            INSERT INTO kill_switches (tenant_id, scope, target_key, engaged, reason,
                                       engaged_by, engaged_at, released_by, released_at)
            VALUES (:t, :scope, :target, :engaged, :reason,
                    CASE WHEN :engaged THEN CAST(:u AS uuid) END,
                    CASE WHEN :engaged THEN now() END,
                    CASE WHEN NOT :engaged THEN CAST(:u AS uuid) END,
                    CASE WHEN NOT :engaged THEN now() END)
            ON CONFLICT (tenant_id, scope, target_key) WHERE tenant_id IS NOT NULL
            DO UPDATE SET engaged = EXCLUDED.engaged, reason = EXCLUDED.reason,
                          engaged_by = EXCLUDED.engaged_by, engaged_at = EXCLUDED.engaged_at,
                          released_by = EXCLUDED.released_by, released_at = EXCLUDED.released_at,
                          updated_at = now()
            """
        ),
        {
            "t": ctx.tenant_id,
            "scope": payload.scope,
            "target": payload.target_key,
            "engaged": payload.engaged,
            "reason": payload.reason,
            "u": ctx.human.user_id,
        },
    )
    from agentic_os.assurance.audit import AuditEntry

    AuditLedger(db).append(
        ctx,
        AuditEntry(
            category="KILL_SWITCH",
            action="killswitch.engaged" if payload.engaged else "killswitch.released",
            resource_type="kill_switch",
            resource_id=f"{payload.scope}:{payload.target_key}",
            payload={"reason": payload.reason},
        ),
    )
    return {"scope": payload.scope, "target_key": payload.target_key, "engaged": payload.engaged}


# -------------------------------------------------------------------- privacy
class DsarRequest(BaseModel):
    request_type: str = Field(pattern="^(ACCESS|EXPORT|DELETE|RECTIFY)$")
    subject_email: str = Field(min_length=3, max_length=320)


@router.get(
    "/privacy",
    dependencies=[Depends(require_permission("privacy:read", resource_type="privacy"))],
)
def privacy_register(ctx: CtxDep, db: DbDep) -> dict:
    """The privacy register: requests, holds, processing activities, PII found."""
    requests = db.execute(
        text(
            "SELECT id, request_type, subject_email, status, due_at, completed_at, "
            "affected_records, created_at FROM data_subject_requests "
            "WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 100"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    holds = db.execute(
        text(
            "SELECT hold_key, reason, resource_type, active, created_at, released_at "
            "FROM legal_holds WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 100"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    activities = db.execute(
        text(
            "SELECT activity, purpose, legal_basis, data_categories, subject_categories, "
            "recipients, cross_border, retention, controller FROM processing_records "
            "WHERE tenant_id = :t ORDER BY activity"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    pii = db.execute(
        text(
            "SELECT pii_type, count(*) AS occurrences, "
            "count(*) FILTER (WHERE redacted) AS redacted "
            "FROM pii_inventory WHERE tenant_id = :t GROUP BY pii_type ORDER BY pii_type"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {
        "requests": json_rows(requests),
        "legal_holds": json_rows(holds),
        "processing_activities": json_rows(activities),
        "pii_summary": json_rows(pii),
    }


@router.post(
    "/privacy/requests",
    dependencies=[Depends(require_permission("privacy:write", resource_type="privacy"))],
)
def raise_dsar(payload: DsarRequest, ctx: CtxDep, db: DbDep) -> dict:
    from agentic_os.privacy import dsar

    try:
        request_id = dsar.raise_request(
            db,
            ctx,
            request_type=payload.request_type,  # type: ignore[arg-type]
            subject_email=payload.subject_email,
        )
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    db.commit()
    return {"request_id": request_id, "status": "RECEIVED"}


@router.post(
    "/privacy/requests/{request_id}/process",
    dependencies=[Depends(require_permission("privacy:write", resource_type="privacy"))],
)
def process_dsar(request_id: str, ctx: CtxDep, db: DbDep) -> dict:
    """Execute a recorded request.

    The export body is deliberately not returned over this endpoint: it is a
    complete dossier on a person and belongs in a delivery channel with its own
    identity verification. The response reports what was collected or changed.
    """
    from agentic_os.privacy import dsar

    try:
        result = dsar.process(db, ctx, request_id)
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    db.commit()
    body = result.to_dict()
    body.pop("export", None)
    return jsonify(body)
