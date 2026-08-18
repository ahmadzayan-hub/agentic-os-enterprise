"""WCAG 2.2 AA conformance, evidenced from a real browser audit.

The audit itself runs in a browser against the running application
(``apps/web/tests/accessibility/axe_audit.mjs``). This test consumes its report,
so the control is evidenced by an actual axe-core run rather than by assertion.

If no report exists the test *skips*, which the Evidence Engine records as
NOT_EVIDENCED — a missing audit must never look like a passing one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentic_os.core.registry import REPO_ROOT

REPORT = REPO_ROOT / "artifacts" / "accessibility.json"

#: A report older than this is treated as stale evidence.
MAX_AGE = timedelta(days=7)

REQUIRED_TAGS = {"wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"}

MINIMUM_SURFACES = 15


@pytest.fixture(scope="module")
def report() -> dict:
    if not REPORT.exists():
        pytest.skip(
            "no accessibility report at artifacts/accessibility.json; run "
            "`npm run a11y` in apps/web against a running application"
        )
    return json.loads(REPORT.read_text(encoding="utf-8"))


@pytest.mark.accessibility
def test_no_serious_or_critical_accessibility_violations(report: dict) -> None:
    offenders = [
        f"{entry['colorScheme']} {entry['path']}: {violation['id']} ({violation['impact']})"
        for entry in report["results"]
        for violation in entry["violations"]
        if violation["impact"] in ("serious", "critical")
    ]
    assert offenders == [], "serious or critical accessibility violations: " + "; ".join(offenders)


@pytest.mark.accessibility
def test_no_violations_of_any_impact(report: dict) -> None:
    assert report["total_violations"] == 0, (
        f"{report['total_violations']} accessibility violations remain across "
        f"{report['surfaces_scanned']} surfaces"
    )


@pytest.mark.accessibility
def test_audit_covers_wcag_22_aa(report: dict) -> None:
    assert REQUIRED_TAGS <= set(report["tags"]), (
        f"audit must cover {sorted(REQUIRED_TAGS)}, ran {report['tags']}"
    )


@pytest.mark.accessibility
def test_audit_covers_both_colour_schemes(report: dict) -> None:
    schemes = {entry["colorScheme"] for entry in report["results"]}
    assert schemes == {"light", "dark"}, f"both colour schemes must be audited, got {schemes}"


@pytest.mark.accessibility
def test_audit_covers_the_primary_surfaces(report: dict) -> None:
    assert report["surfaces_scanned"] >= MINIMUM_SURFACES, (
        f"only {report['surfaces_scanned']} surfaces audited; at least "
        f"{MINIMUM_SURFACES} are required for meaningful coverage"
    )
    paths = {entry["path"] for entry in report["results"]}
    for required in ("/", "/runs", "/approvals", "/governance/evidence", "/login"):
        assert required in paths, f"the audit must cover {required}"


@pytest.mark.accessibility
def test_report_is_not_stale(report: dict) -> None:
    generated = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00"))
    age = datetime.now(UTC) - generated
    assert age < MAX_AGE, (
        f"accessibility evidence is {age.days} days old; re-run the audit "
        f"(evidence older than {MAX_AGE.days} days does not count)"
    )
