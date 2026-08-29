"""Computing KPI values, or declining to.

The audit left `kpi_values` empty and the readiness report said so: definitions
existed and nothing computed them. A KPI nobody measures is a number waiting to
be invented, so this module closes that gap with the same discipline the
confidence calculator uses.

A KPI is computed only if a **named computation is registered for its key**.
There is no generic evaluator that reads ``kpi_definitions.formula`` and tries
to make sense of it: the formula column is prose for a human reviewer, and a
parser guessing at prose is exactly how a dashboard ends up confidently wrong.

Three outcomes, and the difference between the last two is the point:

``COMPUTED``
    A value was produced from recorded data and written with the query results
    that produced it.
``INSUFFICIENT_DATA``
    A computation exists and ran, and the period holds nothing to measure.
    No row is written — an empty period is not a zero.
``NO_COMPUTATION``
    The organisation has defined this KPI and nothing in the platform knows how
    to measure it. No row is written, and the surfaces say so in those words.

That last state is not a defect to be hidden. Two of the seeded KPIs are in it:
point machine failures needs an asset class the incident register does not
carry, and door availability needs service hours the platform never receives.
Deleting those definitions would make this module look complete; leaving them
visible and unmeasured is the honest report of where the platform actually is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext
from agentic_os.core.errors import AuthorizationError

Status = Literal["COMPUTED", "INSUFFICIENT_DATA", "NO_COMPUTATION"]


@dataclass(slots=True)
class Measurement:
    """One computed figure, with the counts it came from."""

    value: float
    sample_count: int
    computed_from: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class KpiOutcome:
    kpi_key: str
    status: Status
    value: float | None = None
    sample_count: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kpi_key": self.kpi_key,
            "status": self.status,
            "value": self.value,
            "sample_count": self.sample_count,
            "reason": self.reason,
        }


class Computation(Protocol):
    """A named measurement over a period.

    Returns ``None`` when the period holds nothing to measure. Returning zero
    instead would be a claim — "we measured, and the answer is nought" — which
    is a different statement from "there was nothing to measure".
    """

    def __call__(
        self, session: Session, tenant_id: str, start: datetime, end: datetime
    ) -> Measurement | None: ...


# --------------------------------------------------------------- computations
def _decision_effectiveness(
    session: Session, tenant_id: str, start: datetime, end: datetime
) -> Measurement | None:
    """The North Star, as a percentage over decisions closed in the period.

    Shares its definition with :mod:`agentic_os.decisions.effectiveness` — the
    denominator is decisions that *reached* verification, so a queue is not
    counted as failure, and only ACHIEVED counts in the numerator.
    """
    row = (
        session.execute(
            text(
                """
                SELECT
                  count(*) FILTER (
                    WHERE d.state IN ('VERIFICATION_PENDING', 'VERIFIED', 'CLOSED')
                  ) AS reached,
                  count(*) FILTER (
                    WHERE d.state = 'VERIFIED' AND o.verdict = 'ACHIEVED'
                  ) AS achieved
                  FROM decisions d
                  LEFT JOIN LATERAL (
                      SELECT verdict FROM decision_outcomes
                       WHERE decision_id = d.id AND tenant_id = d.tenant_id
                       ORDER BY created_at DESC LIMIT 1
                  ) o ON true
                 WHERE d.tenant_id = CAST(:t AS uuid)
                   AND d.updated_at >= :start AND d.updated_at < :end
                """
            ),
            {"t": tenant_id, "start": start, "end": end},
        )
        .mappings()
        .one()
    )
    reached = int(row["reached"])
    if reached == 0:
        return None
    achieved = int(row["achieved"])
    return Measurement(
        value=round(achieved / reached * 100, 6),
        sample_count=reached,
        computed_from={"achieved": achieved, "reached_verification": reached},
    )


def _decision_lead_time_days(
    session: Session, tenant_id: str, start: datetime, end: datetime
) -> Measurement | None:
    """Mean days from a decision being detected to being closed.

    Only closed decisions count. Including open ones would make the figure fall
    every time a new case is raised, which rewards not raising them.
    """
    row = (
        session.execute(
            text(
                """
                SELECT count(*) AS n,
                       avg(EXTRACT(EPOCH FROM (closed_at - created_at)) / 86400.0) AS days
                  FROM decisions
                 WHERE tenant_id = CAST(:t AS uuid)
                   AND closed_at IS NOT NULL
                   AND closed_at >= :start AND closed_at < :end
                """
            ),
            {"t": tenant_id, "start": start, "end": end},
        )
        .mappings()
        .one()
    )
    n = int(row["n"])
    if n == 0 or row["days"] is None:
        return None
    return Measurement(
        value=round(float(row["days"]), 6),
        sample_count=n,
        computed_from={"decisions_closed": n},
    )


def _incident_mttr_hours(
    session: Session, tenant_id: str, start: datetime, end: datetime
) -> Measurement | None:
    """Mean hours from an incident being detected to being resolved."""
    row = (
        session.execute(
            text(
                """
                SELECT count(*) AS n,
                       avg(EXTRACT(EPOCH FROM (resolved_at - detected_at)) / 3600.0) AS hours
                  FROM incidents
                 WHERE tenant_id = CAST(:t AS uuid)
                   AND resolved_at IS NOT NULL
                   AND resolved_at >= :start AND resolved_at < :end
                """
            ),
            {"t": tenant_id, "start": start, "end": end},
        )
        .mappings()
        .one()
    )
    n = int(row["n"])
    if n == 0 or row["hours"] is None:
        return None
    return Measurement(
        value=round(float(row["hours"]), 6),
        sample_count=n,
        computed_from={"incidents_resolved": n},
    )


def _run_success_rate(session: Session, tenant_id: str, start: datetime, end: datetime) -> Measurement | None:
    """Share of finished agent runs that succeeded.

    Runs still in flight are excluded from both sides: a run that has not
    finished has neither succeeded nor failed.
    """
    row = (
        session.execute(
            text(
                """
                SELECT count(*) FILTER (WHERE status IN ('SUCCEEDED', 'FAILED')) AS finished,
                       count(*) FILTER (WHERE status = 'SUCCEEDED') AS succeeded
                  FROM runs
                 WHERE tenant_id = CAST(:t AS uuid)
                   AND created_at >= :start AND created_at < :end
                """
            ),
            {"t": tenant_id, "start": start, "end": end},
        )
        .mappings()
        .one()
    )
    finished = int(row["finished"])
    if finished == 0:
        return None
    succeeded = int(row["succeeded"])
    return Measurement(
        value=round(succeeded / finished * 100, 6),
        sample_count=finished,
        computed_from={"succeeded": succeeded, "finished": finished},
    )


#: The registry. A definition whose key is absent is reported NO_COMPUTATION,
#: never estimated. Adding a key here is the only way to make a KPI measurable,
#: which keeps "what the platform can measure" a short readable list rather
#: than an emergent property of a formula parser.
COMPUTATIONS: dict[str, Computation] = {
    "decision.effectiveness_rate": _decision_effectiveness,
    "decision.lead_time_days": _decision_lead_time_days,
    "incident.mttr_hours": _incident_mttr_hours,
    "run.success_rate": _run_success_rate,
}


# ------------------------------------------------------------------- the pass
def month_bounds(moment: datetime | None = None) -> tuple[datetime, datetime]:
    """The calendar month containing ``moment``, as a half-open interval."""
    now = moment or datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def compute_all(
    session: Session,
    ctx: ExecutionContext,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[KpiOutcome]:
    """Measure every active KPI definition for the period, and record the results.

    Writes one ``kpi_values`` row per KPI that could actually be measured, and
    nothing at all for the others. Re-running for the same period overwrites
    that period's value rather than accumulating duplicates, so the pass is
    safe to schedule.
    """
    human = ctx.human
    if human is not None and "*" not in human.permissions and "kpis:write" not in human.permissions:
        raise AuthorizationError("permission 'kpis:write' is required to record KPI values")

    if start is None or end is None:
        start, end = month_bounds()

    definitions = (
        session.execute(
            text(
                "SELECT id, kpi_key FROM kpi_definitions "
                "WHERE tenant_id = CAST(:t AS uuid) AND status = 'ACTIVE' ORDER BY kpi_key"
            ),
            {"t": ctx.tenant_id},
        )
        .mappings()
        .all()
    )

    outcomes: list[KpiOutcome] = []
    for definition in definitions:
        key = str(definition["kpi_key"])
        computation = COMPUTATIONS.get(key)
        if computation is None:
            outcomes.append(
                KpiOutcome(
                    kpi_key=key,
                    status="NO_COMPUTATION",
                    reason=(
                        "no computation is registered for this key; the platform "
                        "does not hold the data this KPI is defined over"
                    ),
                )
            )
            continue

        measurement = computation(session, ctx.tenant_id, start, end)
        if measurement is None:
            outcomes.append(
                KpiOutcome(
                    kpi_key=key,
                    status="INSUFFICIENT_DATA",
                    reason="nothing to measure in this period; no value recorded",
                )
            )
            continue

        import json

        session.execute(
            text(
                """
                INSERT INTO kpi_values
                    (tenant_id, kpi_definition_id, period_start, period_end, value,
                     sample_count, basis, computed_from)
                VALUES (CAST(:t AS uuid), :d, :start, :end, :value, :n, 'MEASURED',
                        CAST(:src AS jsonb))
                ON CONFLICT (tenant_id, kpi_definition_id, period_start, period_end)
                  DO UPDATE SET value = EXCLUDED.value,
                                sample_count = EXCLUDED.sample_count,
                                computed_from = EXCLUDED.computed_from,
                                computed_at = now()
                """
            ),
            {
                "t": ctx.tenant_id,
                "d": definition["id"],
                "start": start,
                "end": end,
                "value": measurement.value,
                "n": measurement.sample_count,
                "src": json.dumps(measurement.computed_from),
            },
        )
        outcomes.append(
            KpiOutcome(
                kpi_key=key,
                status="COMPUTED",
                value=measurement.value,
                sample_count=measurement.sample_count,
            )
        )

    return outcomes


def computation_status(kpi_key: str) -> str:
    """Whether the platform knows how to measure this KPI at all."""
    return "REGISTERED" if kpi_key in COMPUTATIONS else "NO_COMPUTATION"
