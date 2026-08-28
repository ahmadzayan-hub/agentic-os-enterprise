"""Business value must be measured, never invented."""

from __future__ import annotations

import pytest
from agentic_os.core.context import ExecutionContext, HumanIdentity
from agentic_os.core.errors import ValidationError
from agentic_os.outcomes import engine
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


@pytest.fixture()
def octx(tenant_id: str, organization_id: str) -> ExecutionContext:
    return ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(user_id="00000000-0000-0000-0000-000000000001", email="o@x.test"),
    )


def test_measured_outcome_requires_evidence(db: Session, octx) -> None:
    """A MEASURED outcome without evidence is rejected in code and in the database."""
    with pytest.raises(ValidationError):
        engine.record(
            db,
            octx,
            engine.Outcome(outcome_type="COST_AVOIDED", quantity=1000, basis="MEASURED", evidence_refs=[]),
        )

    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO business_outcomes (tenant_id, outcome_type, quantity, basis, "
                "evidence_refs) VALUES (:t, 'COST_AVOIDED', 1000, 'MEASURED', '[]'::jsonb)"
            ),
            {"t": octx.tenant_id},
        )
        db.flush()
    db.rollback()


def test_estimated_outcome_is_accepted_but_labelled(db: Session, octx) -> None:
    outcome_id = engine.record(
        db,
        octx,
        engine.Outcome(
            outcome_type="HOURS_SAVED",
            quantity=12,
            unit="hours",
            basis="ESTIMATED",
            calculation={"method": "workshop estimate"},
        ),
    )
    basis = db.execute(
        text("SELECT basis FROM business_outcomes WHERE id = CAST(:i AS uuid)"),
        {"i": outcome_id},
    ).scalar_one()
    assert basis == "ESTIMATED"
    db.rollback()


def test_roi_excludes_estimated_outcomes(db: Session, octx) -> None:
    engine.record(
        db,
        octx,
        engine.Outcome(
            outcome_type="COST_AVOIDED",
            quantity=1,
            monetary_value_usd=50_000,
            basis="ESTIMATED",
            calculation={"method": "assumed"},
        ),
    )
    engine.record(
        db,
        octx,
        engine.Outcome(
            outcome_type="COST_AVOIDED",
            quantity=1,
            monetary_value_usd=100,
            basis="MEASURED",
            evidence_refs=[{"type": "invoice", "id": "INV-1"}],
            calculation={"method": "recorded"},
        ),
    )
    db.flush()

    summary = engine.roi_summary(db, octx.tenant_id, window_days=1)
    assert summary["measured_value_usd"] == 100.0, (
        "the 50,000 estimate must not appear in the measured figure"
    )
    assert any(row["outcome_type"] == "COST_AVOIDED" for row in summary["estimated"])
    assert "excluded" in summary["basis_note"].lower()
    db.rollback()


def test_run_automation_outcome_is_measured_from_recorded_data(db: Session, octx) -> None:
    run_id = db.execute(
        text(
            """
            INSERT INTO runs (tenant_id, organization_id, correlation_id, objective, status,
                              duration_ms, started_at, completed_at)
            VALUES (:t, :o, 'cor_test', 'measured probe', 'SUCCEEDED', 60000, now(), now())
            RETURNING id
            """
        ),
        {"t": octx.tenant_id, "o": octx.organization_id},
    ).scalar_one()

    outcome_id = engine.measure_run_automation(db, octx, str(run_id), baseline_minutes=45)
    assert outcome_id is not None
    row = (
        db.execute(
            text(
                "SELECT basis, quantity, monetary_value_usd, calculation, evidence_refs "
                "FROM business_outcomes WHERE id = CAST(:i AS uuid)"
            ),
            {"i": outcome_id},
        )
        .mappings()
        .one()
    )
    assert row["basis"] == "MEASURED"
    assert float(row["quantity"]) == pytest.approx((45 - 1) / 60, rel=1e-3)
    assert float(row["monetary_value_usd"]) == 0.0, (
        "the platform holds no labour rate and must not invent one"
    )
    assert row["evidence_refs"], "a measured outcome must carry evidence"
    db.rollback()


def test_a_failed_run_produces_no_outcome(db: Session, octx) -> None:
    run_id = db.execute(
        text(
            "INSERT INTO runs (tenant_id, organization_id, correlation_id, objective, status) "
            "VALUES (:t, :o, 'cor_test', 'failed probe', 'FAILED') RETURNING id"
        ),
        {"t": octx.tenant_id, "o": octx.organization_id},
    ).scalar_one()
    assert engine.measure_run_automation(db, octx, str(run_id), baseline_minutes=45) is None
    db.rollback()


def test_baseline_must_be_supplied_by_the_organisation(db: Session, octx) -> None:
    run_id = db.execute(
        text(
            "INSERT INTO runs (tenant_id, organization_id, correlation_id, objective, status, "
            "duration_ms) VALUES (:t, :o, 'cor_test', 'probe', 'SUCCEEDED', 1000) RETURNING id"
        ),
        {"t": octx.tenant_id, "o": octx.organization_id},
    ).scalar_one()
    with pytest.raises(ValidationError):
        engine.measure_run_automation(db, octx, str(run_id), baseline_minutes=0)
    db.rollback()


def test_unknown_outcome_type_is_rejected(db: Session, octx) -> None:
    with pytest.raises(ValidationError):
        engine.record(db, octx, engine.Outcome(outcome_type="VIBES_IMPROVED", quantity=1))
