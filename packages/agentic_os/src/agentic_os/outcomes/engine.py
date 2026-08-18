"""Business Outcome Engine.

ROI is only ever computed from outcomes whose basis is MEASURED — that is,
outcomes derived from data the platform actually recorded (run durations, tool
calls, approvals, incidents) and carrying evidence references. Estimated
outcomes are stored, reported and clearly separated, and never enter an ROI
figure.

The alternative — multiplying task counts by an assumed hourly rate and calling
it savings — produces a number nobody can defend in a review. This module
refuses to produce that number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext
from agentic_os.core.errors import ValidationError

Basis = Literal["MEASURED", "ESTIMATED"]

OUTCOME_TYPES = (
    "HOURS_SAVED",
    "REVENUE_CREATED",
    "REVENUE_PROTECTED",
    "COST_AVOIDED",
    "RISK_REDUCED",
    "SLA_IMPROVED",
    "RESPONSE_TIME_REDUCED",
    "TASKS_AUTOMATED",
    "FORECAST_ACCURACY",
    "DECISION_LEADTIME_IMPROVED",
)


@dataclass(slots=True)
class Outcome:
    outcome_type: str
    quantity: float
    unit: str = "unit"
    monetary_value_usd: float = 0.0
    basis: Basis = "ESTIMATED"
    calculation: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    run_id: str | None = None


def record(session: Session, ctx: ExecutionContext, outcome: Outcome) -> str:
    if outcome.outcome_type not in OUTCOME_TYPES:
        raise ValidationError(f"unknown outcome type '{outcome.outcome_type}'")
    if outcome.basis == "MEASURED" and not outcome.evidence_refs:
        raise ValidationError(
            "a MEASURED outcome must carry evidence references; use basis ESTIMATED otherwise"
        )
    row = session.execute(
        text(
            """
            INSERT INTO business_outcomes (tenant_id, run_id, outcome_type, quantity, unit,
                                           monetary_value_usd, basis, calculation, evidence_refs)
            VALUES (:t, :run, :type, :qty, :unit, :value, :basis, CAST(:calc AS jsonb),
                    CAST(:evidence AS jsonb))
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id,
            "run": outcome.run_id or (ctx.run_id or None),
            "type": outcome.outcome_type,
            "qty": outcome.quantity,
            "unit": outcome.unit,
            "value": outcome.monetary_value_usd,
            "basis": outcome.basis,
            "calc": json.dumps(outcome.calculation, default=str),
            "evidence": json.dumps(outcome.evidence_refs, default=str),
        },
    ).one()
    return str(row.id)


def measure_run_automation(
    session: Session, ctx: ExecutionContext, run_id: str, *, baseline_minutes: float
) -> str | None:
    """Record hours saved for one completed run, from recorded execution data.

    ``baseline_minutes`` is the organisation's own measured manual baseline for
    this class of work. It is an input, recorded in the calculation, not an
    assumption this module invents — and the outcome is only MEASURED because
    the run's actual duration and step count come from the platform's own
    records.
    """
    run = session.execute(
        text(
            "SELECT id, status, duration_ms, cost_usd, objective FROM runs "
            "WHERE tenant_id = :t AND id = CAST(:i AS uuid)"
        ),
        {"t": ctx.tenant_id, "i": run_id},
    ).mappings().first()
    if run is None or run["status"] != "SUCCEEDED":
        return None
    if baseline_minutes <= 0:
        raise ValidationError("baseline_minutes must be positive and organisation-measured")

    actual_minutes = (run["duration_ms"] or 0) / 60000.0
    saved_minutes = max(0.0, baseline_minutes - actual_minutes)

    steps = session.execute(
        text("SELECT count(*) FROM run_steps WHERE run_id = CAST(:i AS uuid)"), {"i": run_id}
    ).scalar_one()

    return record(
        session,
        ctx,
        Outcome(
            outcome_type="HOURS_SAVED",
            quantity=round(saved_minutes / 60.0, 4),
            unit="hours",
            monetary_value_usd=0.0,
            basis="MEASURED",
            calculation={
                "baseline_minutes": baseline_minutes,
                "actual_minutes": round(actual_minutes, 4),
                "saved_minutes": round(saved_minutes, 4),
                "steps_executed": int(steps),
                "method": "organisation_baseline_minus_recorded_run_duration",
                "note": (
                    "Monetary value is deliberately zero: converting hours to money "
                    "requires a rate this platform does not hold."
                ),
            },
            evidence_refs=[
                {"type": "run", "id": run_id},
                {"type": "run_steps", "count": int(steps)},
            ],
            run_id=run_id,
        ),
    )


def roi_summary(session: Session, tenant_id: str, *, window_days: int = 30) -> dict[str, Any]:
    """ROI from measured outcomes only, with estimates reported separately."""
    rows = session.execute(
        text(
            """
            SELECT basis, outcome_type, sum(quantity) AS quantity,
                   sum(monetary_value_usd) AS value, count(*) AS records
            FROM business_outcomes
            WHERE tenant_id = :t AND occurred_at >= now() - make_interval(days => :d)
            GROUP BY basis, outcome_type
            ORDER BY basis, outcome_type
            """
        ),
        {"t": tenant_id, "d": window_days},
    ).mappings().all()

    cost = float(
        session.execute(
            text(
                "SELECT COALESCE(sum(cost_usd), 0) FROM cost_records "
                "WHERE tenant_id = :t AND occurred_at >= now() - make_interval(days => :d)"
            ),
            {"t": tenant_id, "d": window_days},
        ).scalar_one()
    )

    measured = [dict(r) for r in rows if r["basis"] == "MEASURED"]
    estimated = [dict(r) for r in rows if r["basis"] == "ESTIMATED"]
    measured_value = sum(float(r["value"] or 0) for r in measured)

    return {
        "window_days": window_days,
        "platform_cost_usd": round(cost, 6),
        "measured_value_usd": round(measured_value, 2),
        "net_value_usd": round(measured_value - cost, 2),
        "roi_ratio": round(measured_value / cost, 3) if cost > 0 else None,
        "measured": measured,
        "estimated": estimated,
        "basis_note": (
            "ROI is computed exclusively from MEASURED outcomes carrying evidence "
            "references. Estimated outcomes are listed for context and are excluded "
            "from every figure above."
        ),
        "monetisation_note": (
            "Non-monetary outcomes such as hours saved are reported in their own units. "
            "The platform does not hold labour rates and will not invent one."
        ),
    }


def operational_summary(session: Session, tenant_id: str, *, window_days: int = 7) -> dict[str, Any]:
    """Operational counters derived from recorded platform activity."""
    row = session.execute(
        text(
            """
            SELECT
              count(*) FILTER (WHERE status = 'SUCCEEDED') AS succeeded,
              count(*) FILTER (WHERE status = 'FAILED') AS failed,
              count(*) FILTER (WHERE status = 'AWAITING_APPROVAL') AS awaiting_approval,
              count(*) AS total,
              COALESCE(avg(duration_ms) FILTER (WHERE status = 'SUCCEEDED'), 0) AS avg_duration_ms,
              COALESCE(sum(cost_usd), 0) AS cost_usd
            FROM runs
            WHERE tenant_id = :t AND created_at >= now() - make_interval(days => :d)
            """
        ),
        {"t": tenant_id, "d": window_days},
    ).mappings().one()

    total = int(row["total"])
    return {
        "window_days": window_days,
        "runs_total": total,
        "runs_succeeded": int(row["succeeded"]),
        "runs_failed": int(row["failed"]),
        "runs_awaiting_approval": int(row["awaiting_approval"]),
        "success_rate": round(int(row["succeeded"]) / total, 4) if total else None,
        "avg_duration_ms": int(row["avg_duration_ms"]),
        "cost_usd": round(float(row["cost_usd"]), 6),
    }
