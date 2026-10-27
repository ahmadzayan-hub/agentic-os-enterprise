"""KPI measurement, and the two different reasons a KPI has no value.

The failure this guards against is a dashboard that fills in. A KPI the
platform cannot measure must not acquire a number, and a period with nothing in
it must not report zero — "we measured and the answer is nought" is a different
claim from "there was nothing to measure", and an executive acts differently on
each.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from agentic_os.core.context import ExecutionContext, HumanIdentity
from agentic_os.core.errors import AuthorizationError
from agentic_os.decisions.kpi import (
    COMPUTATIONS,
    computation_status,
    compute_all,
    month_bounds,
)
from agentic_os.decisions.lifecycle import create_decision, transition
from sqlalchemy import text

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

#: Defined by the business, unmeasurable by the platform. Kept in the seed on
#: purpose; this suite pins that they stay unmeasured rather than acquiring a
#: plausible number.
UNMEASURABLE = ("signalling.point_machine_failures", "rolling_stock.door_availability")


def _ctx(tenant_id: str, organization_id: str, user_id: str, permissions=frozenset({"*"})):
    return ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(
            user_id=user_id,
            email="kpi@rta.example",
            permissions=permissions,
            mfa_satisfied=True,
        ),
    )


@pytest.fixture()
def owner(db, tenant_id, organization_id, seeded):
    user_id = str(
        db.execute(
            text("SELECT id FROM users WHERE tenant_id = CAST(:t AS uuid) LIMIT 1"),
            {"t": tenant_id},
        ).scalar_one()
    )
    return _ctx(tenant_id, organization_id, user_id)


def _values(db, tenant_id: str, *, start=None, end=None) -> dict[str, float]:
    """Recorded values, optionally confined to one period.

    The period filter matters: this database is shared with the seed and with
    whatever a live verification run left behind, so asserting the table is
    globally empty would only hold on a fresh instance. What each test actually
    means is "the pass I just ran wrote nothing", which is a statement about a
    period.
    """
    clause = " AND v.period_start = :start AND v.period_end = :end" if start else ""
    rows = db.execute(
        text(
            "SELECT k.kpi_key, v.value FROM kpi_values v "  # noqa: S608
            "JOIN kpi_definitions k ON k.id = v.kpi_definition_id "
            f"WHERE v.tenant_id = CAST(:t AS uuid){clause}"
        ),
        {"t": tenant_id, **({"start": start, "end": end} if start else {})},
    ).mappings()
    return {str(r["kpi_key"]): float(r["value"]) for r in rows}


# ------------------------------------------------------------- the registry
def test_a_kpi_is_measurable_only_if_a_computation_is_registered() -> None:
    for key in UNMEASURABLE:
        assert key not in COMPUTATIONS
        assert computation_status(key) == "NO_COMPUTATION"
    assert computation_status("decision.effectiveness_rate") == "REGISTERED"


def test_there_is_no_generic_formula_evaluator() -> None:
    """A parser guessing at prose is how a dashboard ends up confidently wrong.

    The formula column is documentation for a human reviewer. If anything ever
    starts reading it as an expression, this test is the place that says why it
    must not.
    """
    import inspect

    from agentic_os.decisions import kpi

    source = inspect.getsource(kpi)
    assert "eval(" not in source
    assert "kpi_definitions.formula" not in source.replace("``kpi_definitions.formula``", "")


# ----------------------------------------------------- nothing to measure yet
def test_an_unmeasurable_kpi_records_no_value(db, owner, tenant_id) -> None:
    """The case this whole module exists for."""
    start, end = month_bounds()
    outcomes = {o.kpi_key: o for o in compute_all(db, owner, start=start, end=end)}
    db.flush()

    for key in UNMEASURABLE:
        assert outcomes[key].status == "NO_COMPUTATION"
        assert outcomes[key].value is None
        assert "does not hold the data" in outcomes[key].reason

    recorded = _values(db, tenant_id, start=start, end=end)
    assert set(recorded) & set(UNMEASURABLE) == set(), "a KPI the platform cannot measure acquired a value"


def test_an_empty_period_records_no_value(db, owner, tenant_id) -> None:
    """Zero would be a measurement. There was nothing to measure."""
    far_past_start = datetime(2019, 1, 1, tzinfo=UTC)
    far_past_end = datetime(2019, 2, 1, tzinfo=UTC)

    outcomes = {o.kpi_key: o for o in compute_all(db, owner, start=far_past_start, end=far_past_end)}
    db.flush()

    assert outcomes["decision.effectiveness_rate"].status == "INSUFFICIENT_DATA"
    assert outcomes["decision.effectiveness_rate"].value is None
    assert _values(db, tenant_id, start=far_past_start, end=far_past_end) == {}


def test_insufficient_data_is_distinct_from_no_computation(db, owner) -> None:
    """A reader acts differently on each, so the API must not conflate them."""
    outcomes = {
        o.kpi_key: o
        for o in compute_all(
            db,
            owner,
            start=datetime(2019, 1, 1, tzinfo=UTC),
            end=datetime(2019, 2, 1, tzinfo=UTC),
        )
    }
    assert outcomes["decision.effectiveness_rate"].status == "INSUFFICIENT_DATA"
    assert outcomes["signalling.point_machine_failures"].status == "NO_COMPUTATION"


# --------------------------------------------------------- a real measurement
def test_a_closed_decision_produces_a_measured_lead_time(db, owner, tenant_id) -> None:
    slug = f"kpi-{uuid.uuid4().hex[:8]}"
    domain_id = str(
        db.execute(
            text(
                "INSERT INTO domains (tenant_id, slug, name) VALUES (CAST(:t AS uuid), :s, 'KPI') "
                "RETURNING id"
            ),
            {"t": tenant_id, "s": slug},
        ).scalar_one()
    )
    decision_id = create_decision(
        db, owner, domain_id=domain_id, reference=f"KPI-{slug}", title="Measured case"
    )
    transition(db, owner, decision_id=decision_id, to_state="ANALYSING")
    transition(db, owner, decision_id=decision_id, to_state="CLOSED")
    db.flush()

    start, end = month_bounds()
    outcomes = {o.kpi_key: o for o in compute_all(db, owner, start=start, end=end)}
    db.flush()

    lead_time = outcomes["decision.lead_time_days"]
    assert lead_time.status == "COMPUTED"
    assert lead_time.value is not None and lead_time.value >= 0
    assert lead_time.sample_count >= 1
    assert "decision.lead_time_days" in _values(db, tenant_id, start=start, end=end)


def test_a_recorded_value_carries_the_counts_it_came_from(db, owner, tenant_id) -> None:
    """A figure nobody can trace back is a figure nobody can challenge."""
    slug = f"kpi-{uuid.uuid4().hex[:8]}"
    domain_id = str(
        db.execute(
            text(
                "INSERT INTO domains (tenant_id, slug, name) VALUES (CAST(:t AS uuid), :s, 'KPI') "
                "RETURNING id"
            ),
            {"t": tenant_id, "s": slug},
        ).scalar_one()
    )
    decision_id = create_decision(
        db, owner, domain_id=domain_id, reference=f"KPI-{slug}", title="Traceable case"
    )
    transition(db, owner, decision_id=decision_id, to_state="ANALYSING")
    transition(db, owner, decision_id=decision_id, to_state="CLOSED")
    db.flush()

    start, end = month_bounds()
    compute_all(db, owner, start=start, end=end)
    db.flush()

    row = (
        db.execute(
            text(
                "SELECT v.basis, v.sample_count, v.computed_from FROM kpi_values v "
                "JOIN kpi_definitions k ON k.id = v.kpi_definition_id "
                "WHERE v.tenant_id = CAST(:t AS uuid) AND k.kpi_key = 'decision.lead_time_days'"
            ),
            {"t": tenant_id},
        )
        .mappings()
        .one()
    )
    assert row["basis"] == "MEASURED"
    assert row["sample_count"] >= 1
    assert row["computed_from"]["decisions_closed"] >= 1


def test_recomputing_a_period_replaces_rather_than_duplicates(db, owner, tenant_id) -> None:
    """The pass has to be safe to schedule."""
    slug = f"kpi-{uuid.uuid4().hex[:8]}"
    domain_id = str(
        db.execute(
            text(
                "INSERT INTO domains (tenant_id, slug, name) VALUES (CAST(:t AS uuid), :s, 'KPI') "
                "RETURNING id"
            ),
            {"t": tenant_id, "s": slug},
        ).scalar_one()
    )
    decision_id = create_decision(
        db, owner, domain_id=domain_id, reference=f"KPI-{slug}", title="Idempotent case"
    )
    transition(db, owner, decision_id=decision_id, to_state="ANALYSING")
    transition(db, owner, decision_id=decision_id, to_state="CLOSED")
    db.flush()

    start, end = month_bounds()
    for _ in range(3):
        compute_all(db, owner, start=start, end=end)
        db.flush()

    count = db.execute(
        text(
            "SELECT count(*) FROM kpi_values v JOIN kpi_definitions k ON k.id = v.kpi_definition_id "
            "WHERE v.tenant_id = CAST(:t AS uuid) AND k.kpi_key = 'decision.lead_time_days'"
        ),
        {"t": tenant_id},
    ).scalar_one()
    assert count == 1


# ------------------------------------------------------------- authorization
def test_recording_kpi_values_requires_permission(db, tenant_id, organization_id, seeded) -> None:
    user_id = str(
        db.execute(
            text("SELECT id FROM users WHERE tenant_id = CAST(:t AS uuid) LIMIT 1"),
            {"t": tenant_id},
        ).scalar_one()
    )
    reader = _ctx(tenant_id, organization_id, user_id, permissions=frozenset({"kpis:read"}))
    with pytest.raises(AuthorizationError) as excinfo:
        compute_all(db, reader)
    assert "kpis:write" in str(excinfo.value)


def test_a_period_is_a_half_open_interval() -> None:
    """Closed on both ends would count a boundary row into two months."""
    start, end = month_bounds(datetime(2026, 3, 15, 12, tzinfo=UTC))
    assert start == datetime(2026, 3, 1, tzinfo=UTC)
    assert end == datetime(2026, 4, 1, tzinfo=UTC)
    assert end - start == timedelta(days=31)
