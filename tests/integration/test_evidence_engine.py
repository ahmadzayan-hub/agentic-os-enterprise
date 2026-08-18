"""The Evidence Engine must derive maturity, never accept an assertion."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.assurance import evidence
from agentic_os.core.ids import utcnow
from tests.conftest import requires_db

pytestmark = pytest.mark.integration


def _control(control_id: str, **overrides) -> evidence.ControlEvidence:
    defaults = {
        "control_id": control_id,
        "domain": "security",
        "title": control_id,
        "weight": 1.0,
        "critical": False,
        "applicable": True,
        "status": "VERIFIED",
    }
    defaults.update(overrides)
    return evidence.ControlEvidence(**defaults)


# ------------------------------------------------------------------ derivation
@pytest.mark.unit
def test_maturity_is_derived_only_from_test_results() -> None:
    """A control whose test did not pass can never reach VERIFIED."""
    catalogue = {
        "controls": [
            {"id": "C1", "domain": "security", "title": "passing", "requirement": "r",
             "weight": 1, "test": "tests/x.py::test_pass", "expected": "e"},
            {"id": "C2", "domain": "security", "title": "failing", "requirement": "r",
             "weight": 1, "test": "tests/x.py::test_fail", "expected": "e"},
            {"id": "C3", "domain": "security", "title": "unmapped", "requirement": "r",
             "weight": 1, "expected": "e"},
            {"id": "C4", "domain": "security", "title": "missing", "requirement": "r",
             "weight": 1, "test": "tests/x.py::test_never_ran", "expected": "e"},
            {"id": "C5", "domain": "security", "title": "skipped", "requirement": "r",
             "weight": 1, "test": "tests/x.py::test_skip", "expected": "e"},
        ]
    }
    outcomes = {
        "tests/x.py::test_pass": evidence.TestOutcome("tests/x.py::test_pass", True, False, 5),
        "tests/x.py::test_fail": evidence.TestOutcome(
            "tests/x.py::test_fail", False, False, 5, "boom"
        ),
        "tests/x.py::test_skip": evidence.TestOutcome("tests/x.py::test_skip", False, True, 0),
    }
    controls = {c.control_id: c for c in evidence.evaluate_controls(catalogue, outcomes)}
    assert controls["C1"].status == "VERIFIED"
    assert controls["C2"].status == "FAILED"
    assert controls["C3"].status == "NOT_EVIDENCED"
    assert controls["C4"].status == "NOT_EVIDENCED"
    assert controls["C5"].status == "NOT_EVIDENCED"

    report = evidence.calculate_maturity(list(controls.values()))
    assert report.score == 20.0, "only the one passing control of five may count"


@pytest.mark.unit
def test_only_verified_statuses_count_toward_the_score() -> None:
    controls = [
        _control("A", status="VERIFIED", weight=2),
        _control("B", status="PRODUCTION_PROVEN", weight=2),
        _control("C", status="IMPLEMENTED", weight=2),
        _control("D", status="TESTED", weight=2),
        _control("E", status="DESIGNED", weight=2),
    ]
    report = evidence.calculate_maturity(controls)
    assert report.score == 40.0, "IMPLEMENTED, TESTED and DESIGNED must not count"


@pytest.mark.unit
def test_critical_failure_blocks_certification() -> None:
    """Even at a high score, a failed critical control blocks certification."""
    controls = [_control(f"OK{i}", weight=9.9) for i in range(10)]
    controls.append(_control("CRIT", weight=1.0, critical=True, status="FAILED"))
    report = evidence.calculate_maturity(controls)
    assert report.score > 98
    assert report.certified is False
    assert "CRIT" in report.critical_blockers


@pytest.mark.unit
def test_certification_requires_a_perfect_score_and_no_blockers() -> None:
    all_verified = [_control(f"C{i}", weight=1, critical=(i == 0)) for i in range(5)]
    report = evidence.calculate_maturity(all_verified)
    assert report.score == 100.0
    assert report.certified is True
    assert report.critical_blockers == []


@pytest.mark.unit
def test_a_critical_control_that_is_merely_unevidenced_also_blocks() -> None:
    controls = [
        _control("OK", weight=99),
        _control("CRIT", weight=1, critical=True, status="NOT_EVIDENCED"),
    ]
    report = evidence.calculate_maturity(controls)
    assert report.certified is False
    assert report.critical_blockers == ["CRIT"]


@pytest.mark.unit
def test_inapplicable_controls_are_excluded_from_both_sides() -> None:
    controls = [
        _control("A", weight=1),
        _control("B", weight=99, applicable=False, status="FAILED"),
    ]
    report = evidence.calculate_maturity(controls)
    assert report.score == 100.0
    assert report.applicable_weight == 1


@pytest.mark.unit
def test_domain_scores_are_reported_separately() -> None:
    controls = [
        _control("S1", domain="security", weight=2),
        _control("S2", domain="security", weight=2, status="FAILED"),
        _control("P1", domain="privacy", weight=1),
    ]
    report = evidence.calculate_maturity(controls)
    assert report.domain_scores["security"]["score"] == 50.0
    assert report.domain_scores["privacy"]["score"] == 100.0
    assert report.domain_scores["security"]["failed"] == 1


# --------------------------------------------------------------------- expiry
@requires_db
def test_expired_evidence_does_not_count(db: Session, tenant_id: str) -> None:
    """Evidence past its control TTL is downgraded and stops contributing."""
    db.execute(
        text(
            """
            INSERT INTO controls (tenant_id, control_id, domain, title, requirement, weight,
                                  critical, evidence_ttl_days)
            VALUES (:t, 'TTL-TEST', 'security', 'ttl probe', 'r', 1, false, 1)
            ON CONFLICT (tenant_id, control_id) DO UPDATE SET evidence_ttl_days = 1
            """
        ),
        {"t": tenant_id},
    )
    db.execute(
        text(
            """
            INSERT INTO evidence (tenant_id, control_id, status, test_id, collected_at, expires_at)
            VALUES (:t, 'TTL-TEST', 'VERIFIED', 'tests/x.py::t', :collected, :expires)
            """
        ),
        {
            "t": tenant_id,
            "collected": utcnow() - timedelta(days=10),
            "expires": utcnow() - timedelta(days=9),
        },
    )
    db.flush()

    expired = evidence.apply_expiry(db, tenant_id)
    assert expired >= 1

    status = db.execute(
        text(
            "SELECT status FROM evidence WHERE tenant_id = :t AND control_id = 'TTL-TEST' "
            "ORDER BY collected_at DESC LIMIT 1"
        ),
        {"t": tenant_id},
    ).scalar_one()
    assert status == "EXPIRED"

    report = evidence.latest_report(db, tenant_id)
    assert report is not None
    ttl_control = next(c for c in report["controls"] if c["control_id"] == "TTL-TEST")
    assert ttl_control["status"] == "EXPIRED"
    db.rollback()


# ------------------------------------------------------------------- parsing
@pytest.mark.unit
def test_junit_parsing_collapses_parameterised_cases(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0"?>
        <testsuites><testsuite name="pytest" tests="3" failures="1" errors="0" skipped="0">
          <testcase classname="tests.x" name="test_a[1]" time="0.01"/>
          <testcase classname="tests.x" name="test_a[2]" time="0.01">
            <failure message="nope">detail</failure>
          </testcase>
          <testcase classname="tests.x" name="test_b" time="0.02"/>
        </testsuite></testsuites>""",
        encoding="utf-8",
    )
    outcomes = evidence.parse_junit(junit)
    assert outcomes["tests/x.py::test_a"].passed is False, (
        "if any parameterisation fails, the control is not verified"
    )
    assert outcomes["tests/x.py::test_b"].passed is True


@pytest.mark.unit
def test_bundle_is_written_and_hashed(tmp_path: Path) -> None:
    report = evidence.calculate_maturity([_control("A", weight=1)])
    result = evidence.write_bundle(report, tmp_path)
    assert Path(result["bundle_path"]).exists()
    assert len(result["bundle_hash"]) == 64
    assert len(result["content_hash"]) == 64


# ------------------------------------------------- catalogue is loadable
@pytest.mark.unit
def test_the_shipped_control_catalogue_loads_and_totals_one_hundred() -> None:
    catalogue = evidence.load_controls()
    assert sum(c["weight"] for c in catalogue["controls"]) == 100
    assert sum(catalogue["domains"].values()) == 100
    assert any(c.get("critical") for c in catalogue["controls"])
