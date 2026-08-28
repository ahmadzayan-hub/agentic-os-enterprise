"""Decision cases, KPIs and the notification inbox.

Authorization appears twice on every route here, deliberately.
``require_permission`` establishes that the caller may perform this *kind* of
action at all; the repository's domain predicate then decides which decisions
they may act on. Neither is sufficient alone: the permission is tenant-wide,
and the membership check does not know what the caller is trying to do.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from agentic_os.api.deps import CtxDep, DbDep, require_permission
from agentic_os.api.serialization import rows as json_rows
from agentic_os.core.db import affected_rows
from agentic_os.decisions import (
    calculate_confidence,
    create_decision,
    decision_effectiveness_rate,
    get_decision,
    list_decisions,
    transition,
    user_domain_ids,
)
from agentic_os.decisions.lifecycle import LEGAL_TRANSITIONS, STATES
from agentic_os.decisions.repository import sees_all_domains

router = APIRouter(tags=["decisions"])


# ----------------------------------------------------------------- request models
class NewDecision(BaseModel):
    domain_id: str
    reference: str = Field(min_length=3, max_length=64)
    title: str = Field(min_length=3, max_length=300)
    summary: str = ""
    detected_by: str = Field(default="HUMAN", pattern="^(SIGNAL|HUMAN|AGENT|SCHEDULE)$")
    detection_source: str = ""
    classification: str = Field(default="INTERNAL", pattern="^(PUBLIC|INTERNAL|CONFIDENTIAL|RESTRICTED)$")
    risk: str = Field(default="MEDIUM", pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$")
    owner_user_id: str | None = None


class StateChange(BaseModel):
    to_state: str
    reason: str = Field(default="", max_length=2000)


class NewOption(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    description: str = ""
    score: float | None = Field(default=None, ge=0, le=1)
    estimated_cost: float | None = None
    currency: str = Field(default="AED", max_length=3)
    risk: str = Field(default="MEDIUM", pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$")
    reversible: bool = True
    is_status_quo: bool = False


class NewEvidence(BaseModel):
    source_kind: str = Field(pattern="^(DOCUMENT|DATASET|METRIC|RUN|INCIDENT|AUDIT|EXTERNAL|HUMAN)$")
    source_ref: str = ""
    summary: str = ""
    authority_weight: float = Field(default=0.5, gt=0, le=1)


class NewRecommendation(BaseModel):
    option_id: str | None = None
    rationale: str = ""
    reasoning_summary: str = Field(
        default="",
        max_length=4000,
        description=(
            "A concise summary of the reasoning shown to reviewers. Never raw "
            "chain-of-thought: the brief forbids exposing it, and a summary is "
            "what a reviewer can actually act on."
        ),
    )
    produced_by: str = Field(default="AGENT", pattern="^(AGENT|HUMAN|HYBRID)$")


class NewOutcome(BaseModel):
    kpi_definition_id: str | None = None
    target_value: float | None = None
    actual_value: float | None = None
    unit: str = "unit"
    verdict: str = Field(pattern="^(ACHIEVED|PARTIAL|NOT_ACHIEVED|UNVERIFIABLE)$")
    verification_method: str = Field(min_length=3, max_length=500)
    notes: str = ""


class NewLesson(BaseModel):
    lesson: str = Field(min_length=10, max_length=4000)
    category: str = Field(default="PROCESS", pattern="^(PROCESS|DATA|MODEL|POLICY|EXECUTION|ESTIMATION)$")


# ------------------------------------------------------------------------ reads
@router.get(
    "/decisions",
    dependencies=[Depends(require_permission("decisions:read", resource_type="decision"))],
)
def decision_queue(
    ctx: CtxDep,
    db: DbDep,
    state: Annotated[list[str] | None, Query()] = None,
    domain_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """The decision queue, already confined to the caller's domains."""
    unknown = sorted(set(state or []) - set(STATES))
    if unknown:
        from agentic_os.core.errors import ValidationError

        raise ValidationError(f"unknown decision states: {unknown}")

    items = list_decisions(db, ctx, states=state, domain_id=domain_id, limit=limit, offset=offset)
    return {
        "items": json_rows(items),
        "count": len(items),
        "scope": {
            "domains": user_domain_ids(db, ctx),
            "sees_all_domains": sees_all_domains(ctx),
        },
    }


@router.get(
    "/decisions/states",
    dependencies=[Depends(require_permission("decisions:read", resource_type="decision"))],
)
def lifecycle_graph() -> dict[str, Any]:
    """The lifecycle itself, so the console renders one graph rather than a copy."""
    return {
        "states": list(STATES),
        "transitions": {s: sorted(d) for s, d in LEGAL_TRANSITIONS.items()},
    }


@router.get(
    "/decisions/effectiveness",
    dependencies=[Depends(require_permission("decisions:read", resource_type="decision"))],
)
def effectiveness(ctx: CtxDep, db: DbDep) -> dict[str, Any]:
    """The North Star. Returns rate: null — never 0 — over an empty set."""
    domains = None if sees_all_domains(ctx) else user_domain_ids(db, ctx)
    return decision_effectiveness_rate(db, tenant_id=ctx.tenant_id, domain_ids=domains).to_dict()


@router.get(
    "/decisions/{decision_id}",
    dependencies=[Depends(require_permission("decisions:read", resource_type="decision"))],
)
def decision_case(ctx: CtxDep, db: DbDep, decision_id: str) -> dict[str, Any]:
    """One case with its options, evidence, history and outcome.

    Raises NOT_FOUND — not FORBIDDEN — when the caller is outside the decision's
    domain, so the response cannot be used to probe for existence.
    """
    case = get_decision(db, ctx, decision_id)
    confidence = calculate_confidence(db, tenant_id=ctx.tenant_id, decision_id=decision_id)
    case["confidence"] = {
        "value": confidence.value,
        "display": confidence.display(),
        "calculation": confidence.calculation(),
    }
    return dict(json_rows([case])[0])


# ----------------------------------------------------------------------- writes
@router.post(
    "/decisions",
    status_code=201,
    dependencies=[Depends(require_permission("decisions:create", resource_type="decision"))],
)
def raise_decision(ctx: CtxDep, db: DbDep, body: NewDecision) -> dict[str, Any]:
    decision_id = create_decision(
        db,
        ctx,
        domain_id=body.domain_id,
        reference=body.reference,
        title=body.title,
        summary=body.summary,
        detected_by=body.detected_by,
        detection_source=body.detection_source,
        classification=body.classification,
        risk=body.risk,
        owner_user_id=body.owner_user_id,
    )
    db.commit()
    return {"id": decision_id, "state": "DETECTED"}


@router.post(
    "/decisions/{decision_id}/transitions",
    dependencies=[Depends(require_permission("decisions:read", resource_type="decision"))],
)
def move_decision(ctx: CtxDep, db: DbDep, decision_id: str, body: StateChange) -> dict[str, Any]:
    """Move a case.

    The route dependency asks only for decisions:read; the transition itself
    demands the permission its destination requires, which differs per state and
    is checked inside the engine. Putting the stricter check here as well would
    duplicate the table and let the two drift.
    """
    get_decision(db, ctx, decision_id)  # domain check first: 404 before anything else
    result = transition(
        db,
        ctx,
        decision_id=decision_id,
        to_state=body.to_state,  # type: ignore[arg-type]
        reason=body.reason,
    )
    db.commit()
    return {
        "id": result.decision_id,
        "from": result.from_state,
        "to": result.to_state,
        "notified": result.notified,
    }


@router.post(
    "/decisions/{decision_id}/options",
    status_code=201,
    dependencies=[Depends(require_permission("decisions:analyse", resource_type="decision"))],
)
def add_option(ctx: CtxDep, db: DbDep, decision_id: str, body: NewOption) -> dict[str, Any]:
    get_decision(db, ctx, decision_id)
    option_id = str(
        db.execute(
            text(
                """
                INSERT INTO decision_options
                    (tenant_id, decision_id, label, description, score, estimated_cost,
                     currency, risk, reversible, is_status_quo)
                VALUES (CAST(:t AS uuid), CAST(:d AS uuid), :label, :desc, :score, :cost,
                        :cur, CAST(:risk AS risk_class), :rev, :sq)
                RETURNING id
                """
            ),
            {
                "t": ctx.tenant_id,
                "d": decision_id,
                "label": body.label,
                "desc": body.description,
                "score": body.score,
                "cost": body.estimated_cost,
                "cur": body.currency,
                "risk": body.risk,
                "rev": body.reversible,
                "sq": body.is_status_quo,
            },
        ).scalar_one()
    )
    db.commit()
    return {"id": option_id}


@router.post(
    "/decisions/{decision_id}/evidence",
    status_code=201,
    dependencies=[Depends(require_permission("decisions:analyse", resource_type="decision"))],
)
def add_evidence(ctx: CtxDep, db: DbDep, decision_id: str, body: NewEvidence) -> dict[str, Any]:
    get_decision(db, ctx, decision_id)
    evidence_id = str(
        db.execute(
            text(
                """
                INSERT INTO decision_evidence
                    (tenant_id, decision_id, source_kind, source_ref, summary, authority_weight)
                VALUES (CAST(:t AS uuid), CAST(:d AS uuid), :kind, :ref, :sum, :auth)
                RETURNING id
                """
            ),
            {
                "t": ctx.tenant_id,
                "d": decision_id,
                "kind": body.source_kind,
                "ref": body.source_ref,
                "sum": body.summary,
                "auth": body.authority_weight,
            },
        ).scalar_one()
    )
    db.commit()
    return {"id": evidence_id}


@router.post(
    "/decisions/{decision_id}/recommendation",
    status_code=201,
    dependencies=[Depends(require_permission("decisions:analyse", resource_type="decision"))],
)
def add_recommendation(ctx: CtxDep, db: DbDep, decision_id: str, body: NewRecommendation) -> dict[str, Any]:
    """Record a recommendation, with a confidence computed here — never supplied.

    There is deliberately no confidence field on the request body. If a caller
    could pass one, the whole calculation would be advisory, and the first
    integration under deadline pressure would post 0.95 and move on.
    """
    import json

    get_decision(db, ctx, decision_id)
    confidence = calculate_confidence(db, tenant_id=ctx.tenant_id, decision_id=decision_id)
    recommendation_id = str(
        db.execute(
            text(
                """
                INSERT INTO recommendations
                    (tenant_id, decision_id, option_id, rationale, reasoning_summary,
                     produced_by, confidence, confidence_calculation)
                VALUES (CAST(:t AS uuid), CAST(:d AS uuid), CAST(NULLIF(:opt, '') AS uuid),
                        :rationale, :summary, :by, :conf, CAST(:calc AS jsonb))
                RETURNING id
                """
            ),
            {
                "t": ctx.tenant_id,
                "d": decision_id,
                "opt": body.option_id or "",
                "rationale": body.rationale,
                "summary": body.reasoning_summary,
                "by": body.produced_by,
                "conf": confidence.value,
                "calc": json.dumps(confidence.calculation()),
            },
        ).scalar_one()
    )
    db.commit()
    return {
        "id": recommendation_id,
        "confidence": confidence.value,
        "confidence_display": confidence.display(),
        "reason": confidence.reason,
    }


@router.post(
    "/decisions/{decision_id}/outcome",
    status_code=201,
    dependencies=[Depends(require_permission("decisions:verify", resource_type="decision"))],
)
def record_outcome(ctx: CtxDep, db: DbDep, decision_id: str, body: NewOutcome) -> dict[str, Any]:
    """Record what actually happened, measured against the target.

    The verifier and the timestamp are taken from the authenticated context, not
    from the body: a caller must not be able to attribute a verification to
    somebody else.
    """
    get_decision(db, ctx, decision_id)
    outcome_id = str(
        db.execute(
            text(
                """
                INSERT INTO decision_outcomes
                    (tenant_id, decision_id, kpi_definition_id, target_value, actual_value,
                     unit, verdict, verification_method, verified_by_user_id, verified_at, notes)
                VALUES (CAST(:t AS uuid), CAST(:d AS uuid), CAST(NULLIF(:kpi, '') AS uuid),
                        :target, :actual, :unit, :verdict, :method,
                        CAST(NULLIF(:by, '') AS uuid), now(), :notes)
                RETURNING id
                """
            ),
            {
                "t": ctx.tenant_id,
                "d": decision_id,
                "kpi": body.kpi_definition_id or "",
                "target": body.target_value,
                "actual": body.actual_value,
                "unit": body.unit,
                "verdict": body.verdict,
                "method": body.verification_method,
                "by": ctx.human.user_id if ctx.human else "",
                "notes": body.notes,
            },
        ).scalar_one()
    )
    db.commit()
    return {"id": outcome_id, "verdict": body.verdict}


@router.post(
    "/decisions/{decision_id}/lessons",
    status_code=201,
    dependencies=[Depends(require_permission("decisions:verify", resource_type="decision"))],
)
def record_lesson(ctx: CtxDep, db: DbDep, decision_id: str, body: NewLesson) -> dict[str, Any]:
    """LEARN: the stage that makes the loop a loop rather than a line."""
    case = get_decision(db, ctx, decision_id)
    lesson_id = str(
        db.execute(
            text(
                """
                INSERT INTO lessons_learned
                    (tenant_id, decision_id, domain_id, lesson, category, recorded_by_user_id)
                VALUES (CAST(:t AS uuid), CAST(:d AS uuid), CAST(:dom AS uuid), :lesson,
                        :cat, CAST(NULLIF(:by, '') AS uuid))
                RETURNING id
                """
            ),
            {
                "t": ctx.tenant_id,
                "d": decision_id,
                "dom": str(case["domain_id"]),
                "lesson": body.lesson,
                "cat": body.category,
                "by": ctx.human.user_id if ctx.human else "",
            },
        ).scalar_one()
    )
    db.commit()
    return {"id": lesson_id}


# -------------------------------------------------------------------------- KPI
@router.get("/kpis", dependencies=[Depends(require_permission("kpis:read", resource_type="kpi"))])
def kpi_list(ctx: CtxDep, db: DbDep) -> dict[str, Any]:
    """KPI definitions with their latest value.

    A definition with no value reports ``latest_value: null`` rather than zero.
    A KPI nobody has measured is not a KPI reading zero.
    """
    return {
        "items": json_rows(
            db.execute(
                text(
                    """
                    SELECT k.id, k.kpi_key, k.name, k.description, k.formula, k.unit,
                           k.direction, k.target_value, k.warning_value, k.status,
                           v.value AS latest_value, v.period_end AS latest_period_end,
                           v.basis AS latest_basis
                      FROM kpi_definitions k
                      LEFT JOIN LATERAL (
                          SELECT value, period_end, basis FROM kpi_values
                           WHERE kpi_definition_id = k.id AND tenant_id = k.tenant_id
                           ORDER BY period_end DESC LIMIT 1
                      ) v ON true
                     WHERE k.tenant_id = :t AND k.status = 'ACTIVE'
                     ORDER BY k.kpi_key
                    """
                ),
                {"t": ctx.tenant_id},
            ).mappings()
        )
    }


# ---------------------------------------------------------------- notifications
@router.get(
    "/notifications",
    dependencies=[Depends(require_permission("notifications:read", resource_type="notification"))],
)
def inbox(ctx: CtxDep, db: DbDep, unread_only: bool = False) -> dict[str, Any]:
    """Your inbox, and only yours.

    The recipient is taken from the authenticated context and is not a
    parameter, so there is no shape of request that reads somebody else's.
    """
    if ctx.human is None:
        return {"items": [], "unread": 0}
    clause = " AND n.read_at IS NULL" if unread_only else ""
    items = json_rows(
        db.execute(
            text(
                f"""
                SELECT n.id, n.kind, n.subject, n.body, n.read_at, n.created_at,
                       n.decision_id, d.reference AS decision_reference, d.state AS decision_state
                  FROM notifications n
                  LEFT JOIN decisions d ON d.id = n.decision_id AND d.tenant_id = n.tenant_id
                 WHERE n.tenant_id = :t AND n.recipient_user_id = CAST(:u AS uuid){clause}
                 ORDER BY n.created_at DESC LIMIT 100
                """  # noqa: S608 - clause is a fixed literal; values stay bound
            ),
            {"t": ctx.tenant_id, "u": ctx.human.user_id},
        ).mappings()
    )
    unread = sum(1 for i in items if i.get("read_at") is None)
    return {"items": items, "unread": unread}


@router.post(
    "/notifications/{notification_id}/read",
    dependencies=[Depends(require_permission("notifications:read", resource_type="notification"))],
)
def mark_read(ctx: CtxDep, db: DbDep, notification_id: str) -> dict[str, Any]:
    if ctx.human is None:
        from agentic_os.core.errors import AuthorizationError

        raise AuthorizationError("a notification belongs to a person")
    updated = affected_rows(
        db.execute(
            text(
                "UPDATE notifications SET read_at = now() "
                "WHERE tenant_id = :t AND id = CAST(:n AS uuid) "
                "AND recipient_user_id = CAST(:u AS uuid) AND read_at IS NULL"
            ),
            {"t": ctx.tenant_id, "n": notification_id, "u": ctx.human.user_id},
        )
    )
    db.commit()
    return {"updated": updated}
