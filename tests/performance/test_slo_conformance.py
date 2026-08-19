"""Assertions over a measured load-test report.

The report is produced by ``scripts/loadtest.py`` against a running API. This
module does not generate load; it holds the measurement to account, the same
split the accessibility suite uses.

Skipping is right for a developer with no server running. It is wrong anywhere
the load test was just supposed to have run, so CI sets
``AGENTIC_REQUIRE_PERF_REPORT`` and a missing report fails instead — a suite
that skips its way to green evidences nothing.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = [pytest.mark.performance]

REPORT = Path(__file__).resolve().parents[2] / "artifacts" / "performance.json"

#: A report older than this describes a build that no longer exists.
MAX_AGE = timedelta(days=7)

#: Generous, because CI hardware is shared and slower than a workstation. It is
#: a regression gate, not a target: an unindexed query or an N+1 turning a 15 ms
#: read into a 2 s one trips it, while ordinary variance does not.
BASELINE_P95_BUDGET_MS = 1500


@pytest.fixture(scope="module")
def report() -> dict:
    if not REPORT.exists():
        message = (
            "no load-test report at artifacts/performance.json; run "
            "`python scripts/loadtest.py` against a running API"
        )
        if os.environ.get("AGENTIC_REQUIRE_PERF_REPORT"):
            pytest.fail(message)
        pytest.skip(message)
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_every_request_succeeded_at_every_concurrency(report: dict) -> None:
    """The finding that matters: does anything break when requests overlap?

    Connection-pool exhaustion, a lost tenant binding under load, or a lease
    race would all show here as a non-2xx, and none of them show in a
    single-threaded test run.
    """
    failures = [
        f"{entry['concurrency']}x {scenario['name']}: "
        f"{scenario['success_rate']:.1%} success, statuses {scenario['status_counts']}"
        f"{' errors ' + '; '.join(scenario['errors']) if scenario['errors'] else ''}"
        for entry in report["passes"]
        for scenario in entry["scenarios"]
        if scenario["success_rate"] < 1.0
    ]
    assert failures == [], "requests failed under concurrency: " + " | ".join(failures)


def test_the_report_characterises_a_curve_not_a_point(report: dict) -> None:
    """One concurrency level cannot separate per-request cost from queueing."""
    levels = [entry["concurrency"] for entry in report["passes"]]
    assert 1 in levels, "a baseline pass at concurrency 1 is required for comparison"
    assert len(levels) >= 2, f"need at least two concurrency levels, got {levels}"


def test_uncontended_latency_has_not_regressed(report: dict) -> None:
    """At concurrency 1 there is no queueing, so this is per-request cost."""
    baseline = next(e for e in report["passes"] if e["concurrency"] == 1)
    slow = [
        f"{scenario['name']} p95 {scenario['p95_ms']}ms"
        for scenario in baseline["scenarios"]
        if scenario.get("p95_ms", 0) > BASELINE_P95_BUDGET_MS
    ]
    assert slow == [], f"uncontended p95 above {BASELINE_P95_BUDGET_MS}ms: {', '.join(slow)}"


def test_the_report_records_the_environment_it_measured(report: dict) -> None:
    """A latency number without its conditions is quotable out of context."""
    environment = report["environment"]
    assert environment["note"], "the report must state what it does not measure"
    assert "not a production capacity statement" in environment["note"]
    assert environment["platform"] and environment["cpu_count"]


def test_report_is_not_stale(report: dict) -> None:
    generated = datetime.strptime(report["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    age = datetime.now(UTC) - generated
    assert age < MAX_AGE, f"performance report is {age.days} days old; re-run the load test"
