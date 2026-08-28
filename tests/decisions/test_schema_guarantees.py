"""The guarantees migration 0013 makes, asserted against a real database.

Every constraint here was probed adversarially before it was written down, and
one of them failed that probe: the confidence CHECK was originally written as
``jsonb_typeof(calculation -> 'inputs') = 'array'``, which with the column's
default ``'{}'`` evaluates to NULL rather than FALSE — and a CHECK constraint
only rejects on FALSE. The guard was inert for exactly the value it existed to
catch. These tests exist so that cannot happen again quietly.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import pytest
from agentic_os.core.db import get_owner_engine
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

NEW_TABLES = (
    "domains",
    "teams",
    "team_members",
    "decisions",
    "decision_options",
    "recommendations",
    "decision_evidence",
    "decision_transitions",
    "actions",
    "kpi_definitions",
    "kpi_values",
    "decision_outcomes",
    "lessons_learned",
    "notifications",
    "policy_results",
)

REAL_CALCULATION = json.dumps(
    {"inputs": [{"name": "evidence_count", "raw": 4, "normalised": 0.8, "weight": 0.3}]}
)


@pytest.fixture()
def scratch() -> Iterator[Session]:
    """A provisioning session that is always rolled back.

    Deliberately not the shared ``provisioning_db`` fixture, which commits. A
    committed probe row here would be permanent: ``decision_transitions`` is
    append-only, so the very trigger under test also prevents cleaning up after
    it, and every run would leave another decision behind forever.
    """
    factory = sessionmaker(bind=get_owner_engine(), expire_on_commit=False, future=True)
    session = factory()
    try:
        session.execute(text("SET ROLE agentic_provisioner"))
        yield session
    finally:
        session.rollback()
        session.execute(text("RESET ROLE"))
        session.close()


@pytest.fixture()
def probe(scratch: Session, seeded):
    """A throwaway domain and decision to aim constraints at."""
    tenant = scratch.execute(text("SELECT id FROM tenants ORDER BY created_at LIMIT 1")).scalar_one()
    slug = f"probe-{uuid.uuid4().hex[:8]}"
    domain = scratch.execute(
        text("INSERT INTO domains (tenant_id, slug, name) VALUES (:t, :s, 'Probe') RETURNING id"),
        {"t": tenant, "s": slug},
    ).scalar_one()
    decision = scratch.execute(
        text(
            "INSERT INTO decisions (tenant_id, domain_id, reference, title) "
            "VALUES (:t, :d, :r, 'Schema probe') RETURNING id"
        ),
        {"t": tenant, "d": domain, "r": f"PROBE-{slug}"},
    ).scalar_one()
    scratch.flush()
    return {"tenant": tenant, "domain": domain, "decision": decision}


def _refused(session: Session, sql: str, params: dict) -> None:
    """Assert the statement is rejected by the database, not by a caller.

    Inside a savepoint, so the refusal unwinds only its own statement and
    leaves the fixture's setup intact for the assertions that follow.
    """
    savepoint = session.begin_nested()
    with pytest.raises(DatabaseError):
        session.execute(text(sql), params)
    savepoint.rollback()


# ------------------------------------------------------------------- isolation
def test_every_new_table_forces_row_level_security(scratch: Session, seeded) -> None:
    """FORCE, not merely ENABLE: ENABLE alone exempts the table owner."""
    rows = (
        scratch.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = ANY(:names)"
            ),
            {"names": list(NEW_TABLES)},
        )
        .mappings()
        .all()
    )
    found = {r["relname"]: (r["relrowsecurity"], r["relforcerowsecurity"]) for r in rows}
    assert set(found) == set(NEW_TABLES), f"missing tables: {set(NEW_TABLES) - set(found)}"
    unforced = [name for name, (enabled, forced) in found.items() if not (enabled and forced)]
    assert unforced == [], f"row level security is not forced on: {unforced}"


def test_a_second_tenant_cannot_see_another_tenants_decisions(db, tenant_id, other_tenant_id) -> None:
    """The brief's target: unauthorized cross-tenant access is zero rows.

    Rebinding the tenant GUC inside one transaction rather than committing and
    reading from a second session. Committing would leave a decision behind
    permanently — the append-only trigger on its transition row means even the
    test's own cleanup could not remove it.
    """
    domain = db.execute(
        text("INSERT INTO domains (tenant_id, slug, name) VALUES (:t, :s, 'Isolation') RETURNING id"),
        {"t": tenant_id, "s": f"iso-{uuid.uuid4().hex[:8]}"},
    ).scalar_one()
    reference = f"ISO-{uuid.uuid4().hex[:8]}"
    db.execute(
        text(
            "INSERT INTO decisions (tenant_id, domain_id, reference, title) "
            "VALUES (:t, :d, :r, 'Visible only to its own tenant')"
        ),
        {"t": tenant_id, "d": domain, "r": reference},
    )
    db.flush()

    seen_by_owner = db.execute(
        text("SELECT count(*) FROM decisions WHERE reference = :r"), {"r": reference}
    ).scalar_one()
    assert seen_by_owner == 1, "the owning tenant must see its own decision"

    # Same connection, same uncommitted transaction, different tenant binding.
    db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": other_tenant_id})
    seen_by_other = db.execute(
        text("SELECT count(*) FROM decisions WHERE reference = :r"), {"r": reference}
    ).scalar_one()
    assert seen_by_other == 0, "a decision leaked across the tenant boundary"


# -------------------------------------------------------------- append-only log
def test_the_transition_log_refuses_update_even_for_the_provisioning_role(scratch, probe) -> None:
    scratch.execute(
        text(
            "INSERT INTO decision_transitions (tenant_id, decision_id, to_state) VALUES (:t, :d, 'DETECTED')"
        ),
        probe_params(probe),
    )
    scratch.flush()
    _refused(
        scratch,
        "UPDATE decision_transitions SET reason = 'rewritten' WHERE decision_id = :d",
        {"d": probe["decision"]},
    )


def test_the_transition_log_refuses_delete(scratch, probe) -> None:
    scratch.execute(
        text(
            "INSERT INTO decision_transitions (tenant_id, decision_id, to_state) VALUES (:t, :d, 'DETECTED')"
        ),
        probe_params(probe),
    )
    scratch.flush()
    _refused(
        scratch,
        "DELETE FROM decision_transitions WHERE decision_id = :d",
        {"d": probe["decision"]},
    )


def test_the_transition_log_refuses_truncate(scratch, probe) -> None:
    """A row trigger alone would miss this; 0009 found the same hole in the ledger."""
    _refused(scratch, "TRUNCATE decision_transitions", {})


# ------------------------------------------------------- confidence is computed
def test_a_confidence_with_the_default_empty_calculation_is_refused(scratch, probe) -> None:
    """The exact case the first version of this constraint let through."""
    _refused(
        scratch,
        "INSERT INTO recommendations (tenant_id, decision_id, confidence) VALUES (:t, :d, 0.87)",
        probe_params(probe),
    )


@pytest.mark.parametrize(
    "calculation",
    ['{"inputs": "trust me"}', '{"inputs": []}', '{"inputs": {}}', "{}"],
    ids=["not-an-array", "empty-array", "an-object", "absent"],
)
def test_a_confidence_without_real_inputs_is_refused(scratch, probe, calculation: str) -> None:
    _refused(
        scratch,
        "INSERT INTO recommendations (tenant_id, decision_id, confidence, confidence_calculation) "
        "VALUES (:t, :d, 0.9, CAST(:c AS jsonb))",
        {**probe_params(probe), "c": calculation},
    )


def test_a_confidence_with_its_inputs_is_accepted(scratch, probe) -> None:
    scratch.execute(
        text(
            "INSERT INTO recommendations (tenant_id, decision_id, confidence, confidence_calculation) "
            "VALUES (:t, :d, 0.87, CAST(:c AS jsonb))"
        ),
        {**probe_params(probe), "c": REAL_CALCULATION},
    )
    scratch.flush()


def test_a_recommendation_may_carry_no_confidence_at_all(scratch, probe) -> None:
    """NULL is the honest answer when the inputs do not support a figure.

    It is what the surfaces render as "Confidence: Not Calculated", and the
    schema must permit it or the calculator would be pushed into inventing one.
    """
    scratch.execute(
        text(
            "INSERT INTO recommendations (tenant_id, decision_id, rationale) "
            "VALUES (:t, :d, 'insufficient evidence to compute a confidence')"
        ),
        probe_params(probe),
    )
    scratch.flush()


# --------------------------------------------------------- verification is real
def test_a_verdict_cannot_be_claimed_without_verification(scratch, probe) -> None:
    _refused(
        scratch,
        "INSERT INTO decision_outcomes (tenant_id, decision_id, verdict) VALUES (:t, :d, 'ACHIEVED')",
        probe_params(probe),
    )


def test_a_verdict_with_a_verifier_and_a_method_is_accepted(scratch, probe) -> None:
    scratch.execute(
        text(
            "INSERT INTO decision_outcomes "
            "(tenant_id, decision_id, verdict, verification_method, verified_at) "
            "VALUES (:t, :d, 'ACHIEVED', 'compared against KPI target', now())"
        ),
        probe_params(probe),
    )
    scratch.flush()


# ---------------------------------------------------------------- other guards
def test_a_decision_must_belong_to_a_domain(scratch, probe) -> None:
    """Domain is the authorization boundary; a decision outside one is unguarded."""
    _refused(
        scratch,
        "INSERT INTO decisions (tenant_id, domain_id, reference, title) "
        "VALUES (:t, NULL, 'NO-DOMAIN', 'unscoped')",
        probe_params(probe),
    )


def test_a_kpi_value_must_have_a_definition(scratch, probe) -> None:
    """A number with no definition is a fake KPI, which the brief forbids."""
    _refused(
        scratch,
        "INSERT INTO kpi_values (tenant_id, kpi_definition_id, period_start, period_end, value) "
        "VALUES (:t, NULL, now(), now(), 1)",
        probe_params(probe),
    )


def test_an_option_score_outside_zero_to_one_is_refused(scratch, probe) -> None:
    _refused(
        scratch,
        "INSERT INTO decision_options (tenant_id, decision_id, label, score) "
        "VALUES (:t, :d, 'out of range', 1.5)",
        probe_params(probe),
    )


def probe_params(probe: dict) -> dict:
    return {"t": probe["tenant"], "d": probe["decision"]}
