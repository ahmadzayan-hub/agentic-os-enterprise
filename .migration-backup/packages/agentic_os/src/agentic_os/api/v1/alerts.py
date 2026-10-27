"""The alert list, and the two things a person can do with it.

Reading an alert is a disclosure twice over: it says something is wrong, and it
says *where*. So the boundary is the same one the decision queue uses, applied
on both axes at once and inside the SQL rather than after it —

* **domain**, so an alert scoped to a domain the caller does not belong to is
  never retrieved, not retrieved and hidden;
* **permission**, so an alert that names `security:read` does not appear to
  somebody who cannot hold that permission. An operator learning that "there is
  a CRITICAL security alert, title withheld" has already learnt the thing worth
  keeping from them.

Both predicates are in the WHERE clause. A caller outside the boundary gets a
result the database found nothing for, and a single alert they may not see is
reported as absent rather than forbidden: 404 on an identifier the caller
supplied confirms nothing, where 403 confirms the identifier is real.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from agentic_os.api.deps import CtxDep, DbDep, require_permission
from agentic_os.api.serialization import rows as json_rows
from agentic_os.observability.alerting import (
    RULES,
    acknowledge,
    evaluate,
    visibility_predicate,
)

router = APIRouter(tags=["alerts"])

LIST_COLUMNS = """
    a.id, a.alert_type, a.severity, a.title, a.detail, a.source, a.status,
    a.dedupe_key, a.occurrence_count, a.escalation_level, a.escalated_at,
    a.required_permission, a.created_at, a.last_seen_at, a.acknowledged_at,
    a.resolved_at, a.assigned_at,
    dom.slug AS domain_slug, dom.name AS domain_name,
    assignee.email AS assigned_to_email,
    ack.email AS acknowledged_by_email
"""

FROM_CLAUSE = """
    FROM alerts a
    LEFT JOIN domains dom ON dom.id = a.domain_id
    LEFT JOIN users assignee ON assignee.id = a.assigned_to_user_id
    LEFT JOIN users ack ON ack.id = a.acknowledged_by
"""


@router.get(
    "/alerts",
    dependencies=[Depends(require_permission("incidents:read", resource_type="alert"))],
)
def list_alerts(
    ctx: CtxDep,
    db: DbDep,
    status: Annotated[str | None, Query(pattern="^(OPEN|ACKNOWLEDGED|RESOLVED|SUPPRESSED)$")] = None,
    severity: Annotated[str | None, Query(pattern="^(INFO|WARNING|CRITICAL)$")] = None,
    mine: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """The alert list, already confined to what this caller may see.

    Defaults to everything rather than to open alerts only: a surface that
    hides resolved alerts by default makes "we had no incidents" and "we
    resolved four" look identical, and the second is the useful answer.
    """
    scope_sql, params = visibility_predicate(ctx)
    filters = ""
    params |= {"t": ctx.tenant_id, "limit": limit, "offset": offset}
    if status:
        filters += " AND a.status = :status"
        params["status"] = status
    if severity:
        filters += " AND a.severity = :severity"
        params["severity"] = severity
    if mine:
        filters += " AND a.assigned_to_user_id = CAST(:actor AS uuid)"

    listing = db.execute(
        text(
            f"SELECT {LIST_COLUMNS} {FROM_CLAUSE} "
            f"WHERE a.tenant_id = CAST(:t AS uuid){scope_sql}{filters} "
            "ORDER BY CASE a.status WHEN 'OPEN' THEN 0 WHEN 'ACKNOWLEDGED' THEN 1 ELSE 2 END, "
            "  CASE a.severity WHEN 'CRITICAL' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END, "
            "  a.created_at DESC "
            "LIMIT :limit OFFSET :offset"
        ),
        params,
    ).mappings()

    # The counts are computed under the same predicate as the listing. A total
    # taken without it would tell the caller how many alerts exist outside
    # their boundary, which is the disclosure the boundary exists to prevent.
    totals = (
        db.execute(
            text(
                "SELECT count(*) AS total, "
                "  count(*) FILTER (WHERE a.status = 'OPEN') AS open, "
                "  count(*) FILTER (WHERE a.status = 'OPEN' "
                "                     AND a.severity = 'CRITICAL') AS critical_open, "
                "  count(*) FILTER (WHERE a.status = 'OPEN' "
                "                     AND a.assigned_to_user_id IS NULL) AS unassigned "
                f"{FROM_CLAUSE} WHERE a.tenant_id = CAST(:t AS uuid){scope_sql}"
            ),
            params,
        )
        .mappings()
        .one()
    )

    return {
        "alerts": json_rows(listing),
        "counts": dict(totals),
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/alerts/{alert_id}/acknowledge",
    dependencies=[Depends(require_permission("incidents:write", resource_type="alert"))],
)
def acknowledge_alert(alert_id: str, ctx: CtxDep, db: DbDep) -> dict:
    """Record that this caller has looked at the alert.

    The visibility predicate is applied first and a miss is a 404 from
    ``acknowledge``: acknowledging an alert one cannot see would otherwise both
    stop its escalation and confirm it exists.
    """
    scope_sql, params = visibility_predicate(ctx)
    visible = db.execute(
        text(
            "SELECT 1 FROM alerts a WHERE a.tenant_id = CAST(:t AS uuid) "  # noqa: S608
            f"AND a.id = CAST(:a AS uuid){scope_sql}"
        ),
        params | {"t": ctx.tenant_id, "a": alert_id},
    ).first()
    if visible is None:
        from agentic_os.core.errors import NotFound

        raise NotFound(f"no alert {alert_id}")

    result = acknowledge(db, ctx, alert_id)
    db.commit()
    return result


# `incidents:write` rather than an operations-specific permission: the first
# draft of this route named `operations:write`, which no role grants and the
# catalogue does not define. `require_permission` would have refused every
# caller forever, and the route would have looked implemented.
@router.post(
    "/alerts/evaluate",
    dependencies=[Depends(require_permission("incidents:write", resource_type="alert"))],
)
def run_evaluation(ctx: CtxDep, db: DbDep) -> dict:
    """Run one alerting pass now.

    Normally a schedule drives this; the route exists so a pass can be run on
    demand and so its outcome — including any rule that failed — is inspectable
    rather than buried in a log. ``failed_rules`` is returned rather than
    logged for exactly that reason.
    """
    result = evaluate(db, ctx)
    db.commit()
    return {"registered_rules": sorted(RULES), **result.to_dict()}
