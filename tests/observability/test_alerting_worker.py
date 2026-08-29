"""Alerting actually runs, without anybody asking it to.

Everything else about alerting can be true — rules registered, alerts routed,
a console page, an API — and the readiness report's complaint still stands if
nothing ever calls the engine. "Observability that will not surface a problem
to a human unprompted" is a statement about the schedule, not about the rules.

So these tests drive the real worker `tick()`, which commits, and clean up
after themselves rather than relying on the rolled-back fixture.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from agentic_os.core.db import provisioning_session_scope
from agentic_os.observability.alerting import ALERTING_RUN_METRIC
from agentic_os.worker.loop import WorkerConfig, tick
from sqlalchemy import text

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


@pytest.fixture(autouse=True)
def _clean(seeded) -> Iterator[None]:
    """Start from no schedule and no alerts; leave the same behind.

    Across every tenant, not just the primary one: a worker pass sweeps them
    all, so a test that tidied only its own would leave the others' rows for
    the next run to trip over.
    """
    _reset()
    try:
        yield
    finally:
        _reset()


def _reset() -> None:
    with provisioning_session_scope() as session:
        session.execute(text("DELETE FROM metric_samples WHERE metric = :m"), {"m": ALERTING_RUN_METRIC})
        session.execute(text("DELETE FROM alerts"))


def _samples() -> int:
    with provisioning_session_scope() as session:
        return int(
            session.execute(
                text("SELECT count(*) FROM metric_samples WHERE metric = :m"),
                {"m": ALERTING_RUN_METRIC},
            ).scalar_one()
        )


def _config() -> WorkerConfig:
    return WorkerConfig(worker_id="pytest-alerting", max_passes=1)


def test_a_worker_pass_runs_alerting_for_every_tenant() -> None:
    result = tick(_config())
    assert result.errors == [], result.errors
    assert result.alerting_ran == result.tenants >= 2, (
        "a worker pass must evaluate alerting for each tenant it sweeps"
    )
    assert _samples() == result.tenants


def test_the_next_pass_does_not_run_it_again() -> None:
    """The interval is the whole reason this is safe to put in the poll loop.

    One rule recomputes the audit ledger's entire hash chain. Without the
    schedule the worker would do that every second, and the alerting system
    would be the outage it was built to report.
    """
    first = tick(_config())
    assert first.alerting_ran > 0

    second = tick(_config())
    assert second.alerting_ran == 0
    assert _samples() == first.alerting_ran, "a second pass recorded another run"


def test_a_pass_with_no_findings_still_counts_as_having_run() -> None:
    """Silence has to be distinguishable from never having looked.

    If the metric were written only when something was raised, a system with
    nothing wrong would look identical to one whose alerting had stopped — and
    every subsequent pass would re-run the expensive rules forever.
    """
    result = tick(_config())
    assert result.alerting_ran == result.tenants
    assert _samples() == result.tenants, "a quiet pass did not record that it ran"
    # And the schedule now holds: the run was recorded, so the next pass skips
    # it. This is the assertion that fails if the metric is only written when
    # something is raised.
    assert tick(_config()).alerting_ran == 0


def test_a_failing_rule_reaches_the_worker_errors() -> None:
    """A rule that cannot run means a condition is no longer being checked.

    That belongs in the errors the operator's log actually shows, not in a
    field on a result object nobody prints. The engine records it either way;
    this is about whether the worker surfaces it.
    """
    from agentic_os.observability import alerting

    def broken(session, tenant_id):
        raise RuntimeError("this rule no longer compiles")

    original = dict(alerting.RULES)
    alerting.RULES["test.broken"] = broken
    try:
        result = tick(_config())
    finally:
        alerting.RULES.clear()
        alerting.RULES.update(original)

    assert any("test.broken" in message for message in result.errors), (
        f"a failed alert rule did not reach the worker's errors: {result.errors}"
    )
    assert any("no longer compiles" in message for message in result.errors)


def test_the_registered_rules_run_clean_against_seeded_data() -> None:
    """No rule in the registry fails on a real tenant.

    Deliberately separate from the engine's own version of this test: that one
    runs against the primary tenant inside a transaction, this one runs against
    every tenant the worker actually sweeps.
    """
    result = tick(_config())
    assert result.errors == [], result.errors
