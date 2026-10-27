"""Reading decisions, with the domain boundary inside the query.

The brief sets a hard target: unauthorized cross-domain data access is zero.
That target is not met by fetching rows and dropping the ones the caller may
not see. Retrieval like that is already a disclosure — the row was read, it sat
in memory, it can be counted, and a bug anywhere downstream turns "filtered"
into "shown". It is the same rule the knowledge layer already follows, where
the ACL predicate lives in the SQL rather than in a list comprehension after
it.

So domain membership is a join, not a filter. A caller outside a domain does
not receive an empty result *after* the database found something; the database
finds nothing. And a decision the caller cannot see is reported as absent
rather than forbidden, because 403 on a specific identifier confirms that the
identifier is real.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext
from agentic_os.core.errors import NotFound

#: Roles that legitimately see across every domain: assurance and oversight
#: functions whose whole job is the view nobody else has. Membership is still
#: how everyone else gets access, and this exemption is enumerated rather than
#: inferred from a wildcard permission so that adding a role cannot widen it by
#: accident.
CROSS_DOMAIN_ROLES = frozenset({"auditor", "governance_admin", "executive"})


def sees_all_domains(ctx: ExecutionContext) -> bool:
    human = ctx.human
    if human is None:
        return False
    return bool(CROSS_DOMAIN_ROLES & set(human.roles))


def user_domain_ids(session: Session, ctx: ExecutionContext) -> list[str]:
    """The domains this principal belongs to."""
    human = ctx.human
    if human is None:
        return []
    rows = session.execute(
        text(
            "SELECT domain_id FROM team_members "
            "WHERE tenant_id = CAST(:t AS uuid) AND user_id = CAST(:u AS uuid)"
        ),
        {"t": ctx.tenant_id, "u": human.user_id},
    ).scalars()
    return [str(r) for r in rows]


def _scope(session: Session, ctx: ExecutionContext) -> tuple[str, dict[str, Any]]:
    """The predicate fragment that confines a query to what the caller may see."""
    if sees_all_domains(ctx):
        return "", {}
    return (
        " AND d.domain_id IN ("
        "SELECT tm.domain_id FROM team_members tm "
        "WHERE tm.tenant_id = d.tenant_id AND tm.user_id = CAST(:actor AS uuid))",
        {"actor": ctx.human.user_id if ctx.human else "00000000-0000-0000-0000-000000000000"},
    )


LIST_COLUMNS = """
    d.id, d.reference, d.title, d.summary, d.state, d.risk, d.classification,
    d.detected_by, d.due_at, d.created_at, d.updated_at,
    dom.slug AS domain_slug, dom.name AS domain_name,
    owner.email AS owner_email
"""


def list_decisions(
    session: Session,
    ctx: ExecutionContext,
    *,
    states: list[str] | None = None,
    domain_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """The decision queue, already confined to what this caller may see."""
    scope_sql, scope_params = _scope(session, ctx)
    filters = ""
    params: dict[str, Any] = {"t": ctx.tenant_id, "limit": min(limit, 200), "offset": max(offset, 0)}
    if states:
        filters += " AND d.state = ANY(CAST(:states AS decision_state[]))"
        params["states"] = states
    if domain_id:
        filters += " AND d.domain_id = CAST(:dom AS uuid)"
        params["dom"] = domain_id

    rows = (
        session.execute(
            text(
                f"""
                SELECT {LIST_COLUMNS}
                  FROM decisions d
                  JOIN domains dom ON dom.id = d.domain_id AND dom.tenant_id = d.tenant_id
                  LEFT JOIN users owner
                         ON owner.id = d.owner_user_id AND owner.tenant_id = d.tenant_id
                 WHERE d.tenant_id = CAST(:t AS uuid){scope_sql}{filters}
                 ORDER BY d.created_at DESC
                 LIMIT :limit OFFSET :offset
                """  # noqa: S608 - fragments are fixed literals; values stay bound
            ),
            {**params, **scope_params},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def get_decision(session: Session, ctx: ExecutionContext, decision_id: str) -> dict[str, Any]:
    """One decision case with everything attached to it.

    Raises :class:`NotFound` when the caller may not see it. Not
    ``AuthorizationError``: a 403 against a specific identifier confirms the
    identifier names something real, which is the disclosure the brief forbids.

    This request now loads the decision payload and every attached child table in
    a single database round trip so the caller does not pay one query per list.
    """
    scope_sql, scope_params = _scope(session, ctx)
    row = (
        session.execute(
            text(
                f"""
                SELECT {LIST_COLUMNS}, d.domain_id, d.owner_user_id, d.raised_by_user_id,
                       d.run_id, d.detection_source, d.closed_at,
                       COALESCE(
                         (
                           SELECT jsonb_agg(to_jsonb(option_row))
                           FROM (
                             SELECT id, label, description, score, estimated_cost, currency,
                                    risk, reversible, is_status_quo
                               FROM decision_options option_row
                              WHERE option_row.tenant_id = CAST(:t AS uuid)
                                AND option_row.decision_id = d.id
                              ORDER BY option_row.score DESC NULLS LAST
                           ) AS option_row
                         ),
                         '[]'::jsonb
                       ) AS options_json,
                       COALESCE(
                         (
                           SELECT row_to_json(recommendation_row)
                           FROM (
                             SELECT id, option_id, rationale, reasoning_summary, produced_by,
                                    confidence, confidence_calculation, created_at
                               FROM recommendations recommendation_row
                              WHERE recommendation_row.tenant_id = CAST(:t AS uuid)
                                AND recommendation_row.decision_id = d.id
                              ORDER BY recommendation_row.created_at DESC
                              LIMIT 1
                           ) AS recommendation_row
                         ),
                         'null'::jsonb
                       ) AS recommendation_json,
                       COALESCE(
                         (
                           SELECT jsonb_agg(to_jsonb(evidence_row))
                           FROM (
                             SELECT id, source_kind, source_ref, summary, authority_weight,
                                    observed_at
                               FROM decision_evidence evidence_row
                              WHERE evidence_row.tenant_id = CAST(:t AS uuid)
                                AND evidence_row.decision_id = d.id
                              ORDER BY evidence_row.observed_at DESC
                           ) AS evidence_row
                         ),
                         '[]'::jsonb
                       ) AS evidence_json,
                       COALESCE(
                         (
                           SELECT jsonb_agg(to_jsonb(transition_row))
                           FROM (
                             SELECT id, from_state, to_state, actor_kind, reason, occurred_at
                               FROM decision_transitions transition_row
                              WHERE transition_row.tenant_id = CAST(:t AS uuid)
                                AND transition_row.decision_id = d.id
                              ORDER BY transition_row.occurred_at ASC
                           ) AS transition_row
                         ),
                         '[]'::jsonb
                       ) AS transitions_json,
                       COALESCE(
                         (
                           SELECT jsonb_agg(to_jsonb(action_row))
                           FROM (
                             SELECT id, title, action_kind, status, reversible, reversal_plan,
                                    started_at, completed_at
                               FROM actions action_row
                              WHERE action_row.tenant_id = CAST(:t AS uuid)
                                AND action_row.decision_id = d.id
                              ORDER BY action_row.created_at ASC
                           ) AS action_row
                         ),
                         '[]'::jsonb
                       ) AS actions_json,
                       COALESCE(
                         (
                           SELECT jsonb_agg(to_jsonb(outcome_row))
                           FROM (
                             SELECT id, kpi_definition_id, target_value, actual_value, unit,
                                    verdict, verification_method, verified_at, notes
                               FROM decision_outcomes outcome_row
                              WHERE outcome_row.tenant_id = CAST(:t AS uuid)
                                AND outcome_row.decision_id = d.id
                              ORDER BY outcome_row.created_at DESC
                           ) AS outcome_row
                         ),
                         '[]'::jsonb
                       ) AS outcomes_json,
                       COALESCE(
                         (
                           SELECT jsonb_agg(to_jsonb(lesson_row))
                           FROM (
                             SELECT id, lesson, category, created_at
                               FROM lessons_learned lesson_row
                              WHERE lesson_row.tenant_id = CAST(:t AS uuid)
                                AND lesson_row.decision_id = d.id
                              ORDER BY lesson_row.created_at ASC
                           ) AS lesson_row
                         ),
                         '[]'::jsonb
                       ) AS lessons_json
                  FROM decisions d
                  JOIN domains dom ON dom.id = d.domain_id AND dom.tenant_id = d.tenant_id
                  LEFT JOIN users owner
                         ON owner.id = d.owner_user_id AND owner.tenant_id = d.tenant_id
                 WHERE d.tenant_id = CAST(:t AS uuid)
                   AND d.id = CAST(:d AS uuid){scope_sql}
                """  # noqa: S608 - fragment is a fixed literal; values stay bound
            ),
            {"t": ctx.tenant_id, "d": decision_id, **scope_params},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotFound(f"decision {decision_id} was not found")

    case = dict(row)
    case["options"] = case.pop("options_json") or []
    case["recommendation"] = case.pop("recommendation_json")
    case["evidence"] = case.pop("evidence_json") or []
    case["transitions"] = case.pop("transitions_json") or []
    case["actions"] = case.pop("actions_json") or []
    case["outcomes"] = case.pop("outcomes_json") or []
    case["lessons"] = case.pop("lessons_json") or []
    return case


def _children(
    session: Session, ctx: ExecutionContext, decision_id: str, select: str, *, order: str
) -> list[dict[str, Any]]:
    """Fetch a child collection.

    Safe to query without re-checking the domain: reaching this point means
    :func:`get_decision` already proved the caller may see the parent, and RLS
    still confines every statement to the caller's tenant.
    """
    rows = (
        session.execute(
            text(
                f"{select} WHERE tenant_id = CAST(:t AS uuid) "  # noqa: S608
                f"AND decision_id = CAST(:d AS uuid) ORDER BY {order}"
            ),
            {"t": ctx.tenant_id, "d": decision_id},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def _one(
    session: Session, ctx: ExecutionContext, decision_id: str, select: str, *, order: str
) -> dict[str, Any] | None:
    rows = _children(session, ctx, decision_id, select, order=order)
    return rows[0] if rows else None
