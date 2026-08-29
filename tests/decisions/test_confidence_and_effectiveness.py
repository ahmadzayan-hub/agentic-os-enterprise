"""Confidence and the North Star rate, with the empty cases pinned down.

The failure this suite exists to prevent is not a wrong number. It is a
plausible number: a confidence that appears because something had to appear
there, and an effectiveness rate of 0% or 100% computed over nothing at all.
Both read as information and are not.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from agentic_os.core.context import ExecutionContext, HumanIdentity
from agentic_os.decisions.confidence import (
    EVIDENCE_SATURATION,
    FRESHNESS_WINDOW,
    WEIGHTS,
    calculate_confidence,
)
from agentic_os.decisions.effectiveness import decision_effectiveness_rate
from agentic_os.decisions.lifecycle import create_decision, transition
from sqlalchemy import text

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


@pytest.fixture()
def bench(db, tenant_id, organization_id, seeded):
    """An empty decision to hang evidence and options on."""
    user_id = str(
        db.execute(
            text("SELECT id FROM users WHERE tenant_id = CAST(:t AS uuid) LIMIT 1"),
            {"t": tenant_id},
        ).scalar_one()
    )
    slug = f"conf-{uuid.uuid4().hex[:8]}"
    domain_id = str(
        db.execute(
            text(
                "INSERT INTO domains (tenant_id, slug, name) VALUES (CAST(:t AS uuid), :s, 'Conf') "
                "RETURNING id"
            ),
            {"t": tenant_id, "s": slug},
        ).scalar_one()
    )
    ctx = ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(
            user_id=user_id,
            email="conf@rta.example",
            permissions=frozenset({"*"}),
            mfa_satisfied=True,
        ),
    )
    decision_id = create_decision(
        db, ctx, domain_id=domain_id, reference=f"CONF-{slug}", title="Confidence bench"
    )
    db.flush()
    return {"ctx": ctx, "id": decision_id, "domain_id": domain_id, "tenant": tenant_id}


def _evidence(db, bench, *, count: int, authority: float = 1.0, age_days: int = 1) -> None:
    for i in range(count):
        db.execute(
            text(
                "INSERT INTO decision_evidence "
                "(tenant_id, decision_id, source_kind, summary, authority_weight, observed_at) "
                "VALUES (CAST(:t AS uuid), CAST(:d AS uuid), 'DOCUMENT', :s, :a, :o)"
            ),
            {
                "t": bench["tenant"],
                "d": bench["id"],
                "s": f"evidence {i}",
                "a": authority,
                "o": NOW - timedelta(days=age_days),
            },
        )
    db.flush()


def _options(db, bench, *scores: float) -> None:
    for i, score in enumerate(scores):
        db.execute(
            text(
                "INSERT INTO decision_options (tenant_id, decision_id, label, score) "
                "VALUES (CAST(:t AS uuid), CAST(:d AS uuid), :l, :s)"
            ),
            {"t": bench["tenant"], "d": bench["id"], "l": f"option {i}", "s": score},
        )
    db.flush()


def _confidence(db, bench):
    return calculate_confidence(db, tenant_id=bench["tenant"], decision_id=bench["id"], now=NOW)


# ------------------------------------------------------- when there is no figure
def test_no_evidence_yields_no_confidence(db, bench) -> None:
    _options(db, bench, 0.9, 0.4)
    result = _confidence(db, bench)
    assert result.value is None
    assert result.is_calculated is False
    assert result.display() == "Not Calculated"
    assert "no evidence" in result.reason


def test_a_single_option_yields_no_confidence(db, bench) -> None:
    """With nothing to compare against, a recommendation has no separation."""
    _evidence(db, bench, count=4)
    _options(db, bench, 0.9)
    result = _confidence(db, bench)
    assert result.value is None
    assert "two scored options" in result.reason


def test_an_uncalculated_confidence_carries_no_inputs(db, bench) -> None:
    """The shape the database CHECK relies on: no figure, no inputs array."""
    result = _confidence(db, bench)
    assert result.inputs == []
    assert result.calculation()["inputs"] == []


def test_the_words_are_the_contract(db, bench) -> None:
    """Not '0%', not 'unknown', not 'low'. The brief names these words."""
    assert _confidence(db, bench).display() == "Not Calculated"


# ---------------------------------------------------------- when there is one
def test_a_confidence_is_the_weighted_sum_of_its_inputs(db, bench) -> None:
    _evidence(db, bench, count=EVIDENCE_SATURATION, authority=1.0, age_days=1)
    _options(db, bench, 1.0, 0.0)
    result = _confidence(db, bench)
    # Every input saturated: count 5/5, all fresh, authority 1.0, separation 1.0.
    assert result.value == pytest.approx(sum(WEIGHTS.values()))
    assert result.value == pytest.approx(1.0)


def test_weak_evidence_lowers_the_figure(db, bench) -> None:
    _evidence(db, bench, count=1, authority=0.25, age_days=1)
    _options(db, bench, 0.55, 0.5)
    result = _confidence(db, bench)
    assert result.value is not None
    assert result.value < 0.5, "one weak source and two near-identical options is not confidence"


def test_stale_evidence_lowers_the_figure(db, bench) -> None:
    """A decision resting on year-old readings is a decision about last year."""
    _evidence(db, bench, count=4, authority=1.0, age_days=FRESHNESS_WINDOW.days + 30)
    _options(db, bench, 1.0, 0.0)
    stale = _confidence(db, bench)

    db.execute(
        text("UPDATE decision_evidence SET observed_at = :o WHERE decision_id = CAST(:d AS uuid)"),
        {"o": NOW - timedelta(days=1), "d": bench["id"]},
    )
    db.flush()
    fresh = _confidence(db, bench)

    assert stale.value is not None and fresh.value is not None
    assert fresh.value > stale.value
    assert fresh.value - stale.value == pytest.approx(WEIGHTS["evidence_recency"])


def test_every_figure_can_be_reconstructed_from_its_own_record(db, bench) -> None:
    _evidence(db, bench, count=3, authority=0.8, age_days=2)
    _options(db, bench, 0.9, 0.3)
    result = _confidence(db, bench)
    assert result.value is not None

    calculation = result.calculation()
    recomputed = sum(i["normalised"] * i["weight"] for i in calculation["inputs"])
    assert recomputed == pytest.approx(result.value, abs=1e-3)
    assert {i["name"] for i in calculation["inputs"]} == set(WEIGHTS)


def test_a_stored_figure_satisfies_the_database_constraint(db, bench) -> None:
    """The calculator and the schema must agree on what 'has inputs' means."""
    import json

    _evidence(db, bench, count=3)
    _options(db, bench, 0.9, 0.3)
    result = _confidence(db, bench)
    db.execute(
        text(
            "INSERT INTO recommendations (tenant_id, decision_id, confidence, confidence_calculation) "
            "VALUES (CAST(:t AS uuid), CAST(:d AS uuid), :c, CAST(:calc AS jsonb))"
        ),
        {
            "t": bench["tenant"],
            "d": bench["id"],
            "c": result.value,
            "calc": json.dumps(result.calculation()),
        },
    )
    db.flush()
    # Read it back: the constraint accepting the row is only half the claim,
    # and an INSERT that wrote nothing would satisfy the other half by default.
    stored = (
        db.execute(
            text(
                "SELECT confidence, confidence_calculation FROM recommendations "
                "WHERE decision_id = CAST(:d AS uuid)"
            ),
            {"d": bench["id"]},
        )
        .mappings()
        .one()
    )
    assert float(stored["confidence"]) == pytest.approx(result.value)
    assert len(stored["confidence_calculation"]["inputs"]) == len(result.inputs)


def test_more_evidence_than_saturation_does_not_keep_raising_it(db, bench) -> None:
    """Volume must not be able to masquerade as rigour."""
    _evidence(db, bench, count=EVIDENCE_SATURATION)
    _options(db, bench, 0.8, 0.2)
    at_saturation = _confidence(db, bench).value
    _evidence(db, bench, count=EVIDENCE_SATURATION * 3)
    assert _confidence(db, bench).value == pytest.approx(at_saturation)


# --------------------------------------------------------------- the North Star
def test_the_rate_is_not_calculated_over_an_empty_set(db, bench) -> None:
    """0% reads as total failure and 100% as total success. Neither is true."""
    report = decision_effectiveness_rate(db, tenant_id=bench["tenant"], domain_ids=[bench["domain_id"]])
    assert report.rate is None
    assert report.display() == "Not Calculated"
    assert report.reached_verification == 0


def test_a_decision_still_in_flight_does_not_count_against_the_rate(db, bench) -> None:
    transition(db, bench["ctx"], decision_id=bench["id"], to_state="ANALYSING")
    db.flush()
    report = decision_effectiveness_rate(db, tenant_id=bench["tenant"], domain_ids=[bench["domain_id"]])
    assert report.rate is None, "a queue is not a failure"
    assert report.in_flight == 1


def test_a_verified_and_achieved_decision_gives_a_full_rate(db, bench) -> None:
    _run_to_verified(db, bench, verdict="ACHIEVED")
    report = decision_effectiveness_rate(db, tenant_id=bench["tenant"], domain_ids=[bench["domain_id"]])
    assert report.rate == pytest.approx(1.0)
    assert report.achieved == 1


def test_a_partial_outcome_does_not_count_as_achieved(db, bench) -> None:
    """A decision that half worked did not work; a rate that says otherwise
    cannot be acted on."""
    _run_to_verified(db, bench, verdict="PARTIAL")
    report = decision_effectiveness_rate(db, tenant_id=bench["tenant"], domain_ids=[bench["domain_id"]])
    assert report.rate == pytest.approx(0.0)
    assert report.achieved == 0
    assert report.reached_verification == 1


def test_the_rate_is_empty_when_the_caller_belongs_to_no_domain(db, bench) -> None:
    _run_to_verified(db, bench, verdict="ACHIEVED")
    report = decision_effectiveness_rate(db, tenant_id=bench["tenant"], domain_ids=[])
    assert report.rate is None
    assert report.reached_verification == 0


def _run_to_verified(db, bench, *, verdict: str) -> None:
    for state in [
        "ANALYSING",
        "RECOMMENDATION_READY",
        "AWAITING_REVIEW",
        "AWAITING_APPROVAL",
        "APPROVED",
        "EXECUTING",
        "VERIFICATION_PENDING",
        "VERIFIED",
    ]:
        transition(db, bench["ctx"], decision_id=bench["id"], to_state=state)
    db.execute(
        text(
            "INSERT INTO decision_outcomes "
            "(tenant_id, decision_id, verdict, verification_method, verified_at) "
            "VALUES (CAST(:t AS uuid), CAST(:d AS uuid), :v, 'compared against KPI target', now())"
        ),
        {"t": bench["tenant"], "d": bench["id"], "v": verdict},
    )
    db.flush()
