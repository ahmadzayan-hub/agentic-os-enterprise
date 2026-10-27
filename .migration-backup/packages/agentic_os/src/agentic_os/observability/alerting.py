"""Raising alerts, routing them to somebody, and escalating what nobody answers.

The `alerts` table existed since migration 0006 and never held a row. Nothing
raised one — the only statement touching it anywhere was the SELECT behind the
operations surface. That is what the readiness report meant by observability
that "would support an investigation and will not surface a problem to a human
unprompted": every fact needed to notice an outage was recorded, and no
mechanism turned any of it into somebody's problem.

The design mirrors the KPI computations deliberately, because the same failure
mode applies. A rule is raised only if it is **registered by name**; there is no
generic threshold evaluator reading conditions out of a table. A rule that
cannot be evaluated is reported as such rather than passing silently, since a
rule that quietly never fires is indistinguishable from a system that is fine.

Three properties do the real work:

Deduplication
    Every alert carries a ``dedupe_key``, and a partial unique index keeps one
    live alert per key. A condition that stays true for six hours produces one
    alert with a rising ``occurrence_count``, not seventy-two. Without this an
    alerting pass is a noise generator, and an operator who learns to ignore
    the list is worse off than one who never had it.

Routing
    An alert names the permission needed to act on it and, where the condition
    belongs to a domain, that domain. Assignment then follows the same rule
    notifications already follow — permission **and** domain membership, never
    one alone — so an alert never discloses that something is wrong somewhere
    the recipient cannot look.

Resolution
    A rule that stops matching resolves its own alert. Alerts that only a human
    can close accumulate until the list is worthless; alerts that close
    themselves when the condition clears stay worth reading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext
from agentic_os.core.db import affected_rows
from agentic_os.core.errors import AuthorizationError, NotFound
from agentic_os.decisions.repository import sees_all_domains

Severity = Literal["INFO", "WARNING", "CRITICAL"]

#: How long an unacknowledged CRITICAL alert may sit before it escalates.
#: Deliberately short: the point of escalation is that the first recipient may
#: be asleep, on leave, or looking at something else.
ESCALATE_CRITICAL_AFTER = timedelta(hours=1)
ESCALATE_WARNING_AFTER = timedelta(hours=24)

#: How far back a security finding still counts as current. See
#: :func:`_recent_security_findings` for why this is a window and not a
#: count of open items.
SECURITY_FINDING_WINDOW = timedelta(days=7)

#: How often a tenant's alerting pass runs.
#:
#: Not every worker pass. The worker polls every one to five seconds, and one
#: of these rules recomputes the audit ledger's entire hash chain — an O(n)
#: scan that took 28 seconds over 700,000 rows when the restore was measured.
#: Running that every second would make the alerting system the outage.
ALERTING_INTERVAL = timedelta(minutes=5)

#: The metric a completed pass records. Also how the next pass knows whether it
#: is due: the schedule lives in the database, not in worker memory, so a
#: restarted worker does not re-run every tenant immediately and two workers do
#: not each keep their own clock.
ALERTING_RUN_METRIC = "worker.alerting"


@dataclass(slots=True)
class Finding:
    """One thing wrong, as a rule reports it."""

    dedupe_key: str
    title: str
    severity: Severity
    detail: dict[str, Any] = field(default_factory=dict)
    domain_id: str | None = None
    required_permission: str = "incidents:read"


class Rule(Protocol):
    """Evaluate a condition over recorded data.

    Returns every finding currently true. An empty list means the condition has
    cleared, and any alert this rule previously raised is resolved — which is
    how the list stays short enough to be worth reading.
    """

    def __call__(self, session: Session, tenant_id: str) -> list[Finding]: ...


# ------------------------------------------------------------------- the rules
def _failed_runs(session: Session, tenant_id: str) -> list[Finding]:
    """Runs that failed in the last day, grouped by the error they failed with.

    Grouped rather than one alert per run: fifty runs failing on one broken
    tool is one problem, and fifty alerts about it is a way of hiding that.
    """
    rows = session.execute(
        text(
            """
            SELECT coalesce(NULLIF(error_class, ''), 'UNKNOWN') AS error_class,
                   count(*) AS failures
              FROM runs
             WHERE tenant_id = CAST(:t AS uuid)
               AND status = 'FAILED'
               AND completed_at > now() - interval '1 day'
             GROUP BY 1
             HAVING count(*) >= 3
            """
        ),
        {"t": tenant_id},
    ).mappings()
    return [
        Finding(
            dedupe_key=f"runs.failing:{r['error_class']}",
            title=f"{r['failures']} runs failed with {r['error_class']} in the last day",
            severity="WARNING",
            detail={"error_class": r["error_class"], "failures": int(r["failures"])},
            required_permission="runs:read",
        )
        for r in rows
    ]


def _kpi_breaching_target(session: Session, tenant_id: str) -> list[Finding]:
    """A measured KPI on the wrong side of its own warning threshold.

    Reads the definition's ``direction``, so a KPI where lower is better is not
    reported as healthy for falling. Only MEASURED values count: an estimate
    breaching a target is not evidence of anything.
    """
    rows = session.execute(
        text(
            """
            SELECT k.kpi_key, k.name, k.unit, k.direction, k.warning_value,
                   k.target_value, k.domain_id, v.value, v.period_end
              FROM kpi_definitions k
              JOIN LATERAL (
                  SELECT value, period_end FROM kpi_values
                   WHERE kpi_definition_id = k.id AND tenant_id = k.tenant_id
                     AND basis = 'MEASURED'
                   ORDER BY period_end DESC LIMIT 1
              ) v ON true
             WHERE k.tenant_id = CAST(:t AS uuid)
               AND k.status = 'ACTIVE'
               AND k.warning_value IS NOT NULL
               AND ((k.direction = 'UP_IS_GOOD'   AND v.value < k.warning_value)
                 OR (k.direction = 'DOWN_IS_GOOD' AND v.value > k.warning_value))
            """
        ),
        {"t": tenant_id},
    ).mappings()
    findings = []
    for r in rows:
        breached_target = r["target_value"] is not None and (
            (r["direction"] == "UP_IS_GOOD" and r["value"] < r["target_value"])
            or (r["direction"] == "DOWN_IS_GOOD" and r["value"] > r["target_value"])
        )
        findings.append(
            Finding(
                dedupe_key=f"kpi.breach:{r['kpi_key']}",
                title=(
                    f"{r['name']} is {r['value']:g} {r['unit']}, past its warning "
                    f"threshold of {r['warning_value']:g}"
                ),
                severity="CRITICAL" if breached_target else "WARNING",
                detail={
                    "kpi_key": str(r["kpi_key"]),
                    "value": float(r["value"]),
                    "warning_value": float(r["warning_value"]),
                    "target_value": float(r["target_value"]) if r["target_value"] else None,
                    "direction": str(r["direction"]),
                },
                domain_id=str(r["domain_id"]) if r["domain_id"] else None,
                required_permission="kpis:read",
            )
        )
    return findings


def _overdue_decisions(session: Session, tenant_id: str) -> list[Finding]:
    """Decisions past their due date that nobody has closed."""
    rows = session.execute(
        text(
            """
            SELECT id, reference, title, domain_id, due_at, state
              FROM decisions
             WHERE tenant_id = CAST(:t AS uuid)
               AND due_at IS NOT NULL AND due_at < now()
               AND state NOT IN ('VERIFIED', 'CLOSED', 'REJECTED')
            """
        ),
        {"t": tenant_id},
    ).mappings()
    return [
        Finding(
            dedupe_key=f"decision.overdue:{r['reference']}",
            title=f"{r['reference']} is overdue and still {r['state']}",
            severity="WARNING",
            detail={"decision_id": str(r["id"]), "state": str(r["state"])},
            domain_id=str(r["domain_id"]),
            required_permission="decisions:read",
        )
        for r in rows
    ]


def _broken_audit_chain(session: Session, tenant_id: str) -> list[Finding]:
    """The ledger's hash chain, recomputed.

    The most serious condition the platform can report about itself, so it is
    CRITICAL unconditionally. A broken chain means either corruption or
    tampering, and neither is a warning.
    """
    row = (
        session.execute(text("SELECT broken_at FROM audit_verify_chain(CAST(:t AS uuid))"), {"t": tenant_id})
        .mappings()
        .first()
    )
    if row is None or row["broken_at"] is None:
        return []
    return [
        Finding(
            dedupe_key="audit.chain_broken",
            title="The audit ledger's hash chain is broken",
            severity="CRITICAL",
            detail={"broken_at_sequence": int(row["broken_at"])},
            required_permission="audit:verify",
        )
    ]


def _recent_security_findings(session: Session, tenant_id: str) -> list[Finding]:
    """HIGH and CRITICAL security findings raised in the last week.

    Deliberately a *recency* window rather than an unresolved-count, because
    `security_findings` has no resolution state — no status column, no
    resolved_at, nothing a person can close. A rule reading `status <>
    'RESOLVED'` was the first version of this and it would have crashed on
    every pass, which the failed-rules record would have reported but which is
    not a capability worth registering.

    Giving findings a lifecycle is a real piece of work and belongs to the
    security surface, not to alerting. Until it exists, this reports what the
    schema can actually answer: something serious was found recently. The alert
    clears on its own as the finding ages past the window.
    """
    rows = session.execute(
        text(
            """
            SELECT severity, count(*) AS n
              FROM security_findings
             WHERE tenant_id = CAST(:t AS uuid)
               AND severity IN ('HIGH', 'CRITICAL')
               AND created_at > now() - CAST(:window AS interval)
             GROUP BY severity
            """
        ),
        {"t": tenant_id, "window": f"{int(SECURITY_FINDING_WINDOW.total_seconds())} seconds"},
    ).mappings()
    return [
        Finding(
            dedupe_key=f"security.recent:{r['severity']}",
            title=(
                f"{r['n']} {r['severity']} security findings in the last {SECURITY_FINDING_WINDOW.days} days"
            ),
            severity="CRITICAL" if r["severity"] == "CRITICAL" else "WARNING",
            detail={"severity": str(r["severity"]), "count": int(r["n"])},
            required_permission="security:read",
        )
        for r in rows
    ]


#: The registry. A condition the platform can notice is one that appears here;
#: there is no table of thresholds a caller can extend at runtime, because a
#: rule nobody has read is a rule nobody can trust.
RULES: dict[str, Rule] = {
    "runs.failing": _failed_runs,
    "kpi.breach": _kpi_breaching_target,
    "decision.overdue": _overdue_decisions,
    "audit.chain_broken": _broken_audit_chain,
    "security.recent": _recent_security_findings,
}


# -------------------------------------------------------------------- the pass
@dataclass(slots=True)
class AlertingResult:
    raised: int = 0
    updated: int = 0
    resolved: int = 0
    assigned: int = 0
    escalated: int = 0
    failed_rules: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raised": self.raised,
            "updated": self.updated,
            "resolved": self.resolved,
            "assigned": self.assigned,
            "escalated": self.escalated,
            "failed_rules": self.failed_rules,
        }


def evaluate(
    session: Session,
    ctx: ExecutionContext,
    *,
    now: datetime | None = None,
    rules: dict[str, Rule] | None = None,
) -> AlertingResult:
    """Run every rule, reconcile the alert list, route and escalate.

    A rule that raises is recorded in ``failed_rules`` and does **not** resolve
    the alerts it previously raised. Treating a crashed rule as "condition
    cleared" would close a live alert because the code that noticed it broke,
    which is the worst possible direction for that error.

    ``rules`` defaults to the registry. It is overridable so that the
    reconciliation itself — deduplication, resolution, the crashed-rule case —
    can be exercised against a condition the test controls, rather than by
    arranging seeded data to be wrong in five specific ways and hoping it stays
    that way. Nothing in the running system passes it.
    """
    moment = now or datetime.now(UTC)
    result = AlertingResult()

    for name, rule in (RULES if rules is None else rules).items():
        try:
            findings = rule(session, ctx.tenant_id)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            result.failed_rules[name] = f"{type(exc).__name__}: {exc}"
            continue

        live_keys = {f.dedupe_key for f in findings}
        for finding in findings:
            if _upsert(session, ctx, name, finding, moment):
                result.raised += 1
            else:
                result.updated += 1

        # Anything this rule raised before and no longer reports has cleared.
        result.resolved += _resolve_cleared(session, ctx, name, live_keys, moment)

    result.assigned = _assign_unowned(session, ctx, moment)
    result.escalated = _escalate_unanswered(session, ctx, moment)
    return result


def _upsert(
    session: Session,
    ctx: ExecutionContext,
    rule_name: str,
    finding: Finding,
    moment: datetime,
) -> bool:
    """Raise the alert, or record that an existing condition is still true.

    Returns True when a new alert was created. The partial unique index is what
    makes this safe under concurrency: two evaluators racing produce one alert.

    ``alert_type`` is stamped with the *rule* that raised it, passed in rather
    than parsed off the front of the dedupe key. Resolution keys off this
    column, so deriving it would silently couple every rule's name to its key
    format: a rule that later chose a key without the expected prefix would
    stop resolving its own alerts, and nothing would fail loudly.
    """
    updated = affected_rows(
        session.execute(
            text(
                """
                UPDATE alerts
                   SET last_seen_at = :now,
                       occurrence_count = occurrence_count + 1,
                       severity = :severity,
                       title = :title,
                       detail = CAST(:detail AS jsonb)
                 WHERE tenant_id = CAST(:t AS uuid)
                   AND dedupe_key = :key
                   AND status <> 'RESOLVED'
                """
            ),
            {
                "now": moment,
                "severity": finding.severity,
                "title": finding.title,
                "detail": json.dumps(finding.detail, default=str),
                "t": ctx.tenant_id,
                "key": finding.dedupe_key,
            },
        )
    )
    if updated:
        return False

    session.execute(
        text(
            """
            INSERT INTO alerts (tenant_id, alert_type, severity, title, detail, source,
                                status, dedupe_key, domain_id, required_permission,
                                last_seen_at)
            VALUES (CAST(:t AS uuid), :type, :severity, :title, CAST(:detail AS jsonb),
                    'alerting', 'OPEN', :key, CAST(NULLIF(:dom, '') AS uuid), :perm, :now)
            """
        ),
        {
            "t": ctx.tenant_id,
            "type": rule_name,
            "severity": finding.severity,
            "title": finding.title,
            "detail": json.dumps(finding.detail, default=str),
            "key": finding.dedupe_key,
            "dom": finding.domain_id or "",
            "perm": finding.required_permission,
            "now": moment,
        },
    )
    return True


def _resolve_cleared(
    session: Session,
    ctx: ExecutionContext,
    rule_name: str,
    live_keys: set[str],
    moment: datetime,
) -> int:
    """Close alerts this rule raised and no longer reports."""
    return affected_rows(
        session.execute(
            text(
                """
                UPDATE alerts
                   SET status = 'RESOLVED', resolved_at = :now
                 WHERE tenant_id = CAST(:t AS uuid)
                   AND status <> 'RESOLVED'
                   AND alert_type = :type
                   AND dedupe_key NOT IN (
                         SELECT jsonb_array_elements_text(CAST(:live AS jsonb)))
                """
            ),
            # A JSON array rather than a bound Python list: an empty list has no
            # unambiguous array type on the wire, and this path runs with an
            # empty set every time a condition clears — the one case that must
            # work.
            {
                "now": moment,
                "t": ctx.tenant_id,
                "type": rule_name,
                "live": json.dumps(sorted(live_keys)),
            },
        )
    )


def _assign_unowned(session: Session, ctx: ExecutionContext, moment: datetime) -> int:
    """Give every open alert a person.

    The candidate must hold the permission the alert names *and* belong to its
    domain where it has one, *and* hold a grant that has not expired. An alert
    routed on permission alone would tell somebody that a domain they cannot
    open has a problem in it, which is a disclosure rather than a courtesy.

    The candidate is computed in a subquery over `alerts` rather than a LATERAL
    in the UPDATE's own FROM: PostgreSQL will not let a lateral item reference
    the update target, and the correlated-scalar alternative has to repeat the
    whole subquery in an EXISTS to avoid stamping `assigned_at` on rows that
    found nobody.
    """
    return affected_rows(
        session.execute(
            text(
                """
                UPDATE alerts a
                   SET assigned_to_user_id = c.user_id, assigned_at = :now
                  FROM (
                      SELECT al.id AS alert_id, cand.user_id
                        FROM alerts al
                        CROSS JOIN LATERAL (
                            SELECT ur.user_id
                              FROM user_roles ur
                              JOIN role_permissions rp ON rp.role_id = ur.role_id
                             WHERE ur.tenant_id = al.tenant_id
                               AND rp.permission_id = al.required_permission
                               AND (ur.expires_at IS NULL OR ur.expires_at > :now)
                               AND (al.domain_id IS NULL OR EXISTS (
                                     SELECT 1 FROM team_members tm
                                      WHERE tm.tenant_id = al.tenant_id
                                        AND tm.domain_id = al.domain_id
                                        AND tm.user_id = ur.user_id))
                             ORDER BY ur.user_id
                             LIMIT 1
                        ) AS cand
                       WHERE al.tenant_id = CAST(:t AS uuid)
                         AND al.status = 'OPEN'
                         AND al.assigned_to_user_id IS NULL
                  ) AS c
                 WHERE a.id = c.alert_id
                """
            ),
            {"now": moment, "t": ctx.tenant_id},
        )
    )


def _escalate_unanswered(session: Session, ctx: ExecutionContext, moment: datetime) -> int:
    """Raise the escalation level on alerts nobody has acknowledged.

    Escalation does not reassign or notify anyone else yet — there is no
    on-call rota to escalate *to*, and inventing one would be a decorative
    control. What it does is make the age of an unanswered alert visible and
    sortable, which is the part that can be honestly built today.
    """
    return affected_rows(
        session.execute(
            text(
                """
                UPDATE alerts
                   SET escalation_level = escalation_level + 1, escalated_at = :now
                 WHERE tenant_id = CAST(:t AS uuid)
                   -- Acknowledgement is expressed by the status, not by a
                   -- second `acknowledged_at IS NULL` test. That clause was
                   -- here and a probe showed it changed no outcome: OPEN
                   -- already excludes an acknowledged alert. It was also
                   -- wrong for the case it looked like it was covering — the
                   -- schema permits an acknowledged alert to be put back to
                   -- OPEN, and such an alert must escalate again, which the
                   -- redundant clause would have silently prevented.
                   AND status = 'OPEN'
                   AND created_at < :now - CASE severity
                         WHEN 'CRITICAL' THEN CAST(:critical AS interval)
                         WHEN 'WARNING'  THEN CAST(:warning AS interval)
                         ELSE CAST(:never AS interval) END
                   -- Re-escalation waits the same period again, per severity. A
                   -- fixed hourly re-check would have escalated a day-old
                   -- WARNING twenty-four times over, turning a level meant to
                   -- read as urgency into a clock.
                   AND (escalated_at IS NULL OR escalated_at < :now - CASE severity
                         WHEN 'CRITICAL' THEN CAST(:critical AS interval)
                         WHEN 'WARNING'  THEN CAST(:warning AS interval)
                         ELSE CAST(:never AS interval) END)
                """
            ),
            {
                "now": moment,
                "t": ctx.tenant_id,
                "critical": f"{int(ESCALATE_CRITICAL_AFTER.total_seconds())} seconds",
                "warning": f"{int(ESCALATE_WARNING_AFTER.total_seconds())} seconds",
                "never": "100 years",
            },
        )
    )


def acknowledge(session: Session, ctx: ExecutionContext, alert_id: str) -> dict[str, Any]:
    """Record that a named person has looked at this alert.

    The acknowledger comes from the authenticated context, never the request:
    an acknowledgement attributable to somebody who did not make it is worse
    than none, because it stops the alert escalating.
    """
    human = ctx.human
    if human is None:
        raise AuthorizationError("acknowledging an alert requires a human principal")

    updated = affected_rows(
        session.execute(
            text(
                """
                UPDATE alerts
                   SET status = 'ACKNOWLEDGED',
                       acknowledged_by = CAST(:u AS uuid),
                       acknowledged_at = now()
                 WHERE tenant_id = CAST(:t AS uuid)
                   AND id = CAST(:a AS uuid)
                   AND status = 'OPEN'
                """
            ),
            {"u": human.user_id, "t": ctx.tenant_id, "a": alert_id},
        )
    )
    if not updated:
        raise NotFound(f"no open alert {alert_id}")
    return {"id": alert_id, "status": "ACKNOWLEDGED", "acknowledged_by": human.email}


# --------------------------------------------------------------------- reading
def visibility_predicate(ctx: ExecutionContext) -> tuple[str, dict[str, Any]]:
    """The predicate confining a query to the alerts this caller may see.

    It lives beside the engine rather than in the router because more than one
    surface reads this table. `/v1/incidents` returned every alert in the
    tenant, which was harmless while nothing ever raised one and became a
    cross-domain disclosure the moment something did. A boundary that only one
    of two readers applies is not a boundary.

    The fragment assumes the alerts table is aliased `a`.

    Two conditions, both required. The permission test uses the caller's own
    granted set rather than a join back to `role_permissions`, because the
    context is what every other authorization decision in the API is made from
    and a second source of truth for the same fact is how the two drift apart.

    A wildcard grant satisfies the permission half — the platform already
    treats `*` and `resource:*` as real grants everywhere else, and inventing a
    stricter rule here would only mean an administrator silently stopped seeing
    alerts.
    """
    human = ctx.human
    if human is None:
        # No principal, no alerts. Nothing legitimately reaches this route
        # without one; returning everything on the assumption that it cannot
        # happen is the shape of failure worth refusing outright.
        return " AND false", {}

    params: dict[str, Any] = {"actor": human.user_id}
    clauses = []

    if not sees_all_domains(ctx):
        clauses.append(
            "(a.domain_id IS NULL OR a.domain_id IN ("
            "SELECT tm.domain_id FROM team_members tm "
            "WHERE tm.tenant_id = a.tenant_id AND tm.user_id = CAST(:actor AS uuid)))"
        )

    if "*" not in human.permissions:
        # `held` includes the resource wildcards the caller actually has, so
        # the comparison stays a plain membership test in SQL.
        held = set(human.permissions)
        held |= {p for p in human.permissions if p.endswith(":*")}
        params["held"] = sorted(held)
        clauses.append(
            "(a.required_permission = '' "
            " OR a.required_permission = ANY(CAST(:held AS text[])) "
            " OR split_part(a.required_permission, ':', 1) || ':*' "
            "      = ANY(CAST(:held AS text[])))"
        )

    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def due(
    session: Session,
    ctx: ExecutionContext,
    *,
    interval: timedelta = ALERTING_INTERVAL,
    now: datetime | None = None,
) -> bool:
    """Whether this tenant is due an alerting pass.

    Read from `metric_samples` rather than held in the worker, for the reason
    the worker's own docstring gives about everything else it does: a process
    that keeps the schedule in memory re-runs every tenant on restart, and two
    processes each keep a private clock that neither can see.
    """
    moment = now or datetime.now(UTC)
    last = session.execute(
        text(
            "SELECT max(recorded_at) FROM metric_samples WHERE tenant_id = CAST(:t AS uuid) AND metric = :m"
        ),
        {"t": ctx.tenant_id, "m": ALERTING_RUN_METRIC},
    ).scalar()
    return last is None or last <= moment - interval
