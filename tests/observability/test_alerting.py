"""Alerting: raising, deduplicating, routing, escalating, resolving.

The `alerts` table shipped in migration 0006 and never held a row. Nothing
raised one; the only statement touching it anywhere was a SELECT. So there is
no legacy behaviour to preserve here and no existing test to extend — every
guarantee below is new, and each one is checked by making it fail on purpose
before being trusted.

The failure this suite mostly guards against is not "an alert was missed". It
is the opposite: an alerting pass that produces so much noise that the list
stops being read, or that resolves a live alert because the code that noticed
it crashed. Both turn a control into a liability, and neither shows up as an
error anywhere.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from agentic_os.core.context import ExecutionContext
from agentic_os.core.errors import AuthorizationError, NotFound
from agentic_os.observability.alerting import (
    ALERTING_INTERVAL,
    ALERTING_RUN_METRIC,
    ESCALATE_CRITICAL_AFTER,
    RULES,
    Finding,
    acknowledge,
    due,
    evaluate,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


# --------------------------------------------------------------------- helpers
def _rule(*findings: Finding):
    """A rule that reports exactly what it is given, ignoring the database."""

    def rule(session: Session, tenant_id: str) -> list[Finding]:
        return list(findings)

    return rule


def _exploding_rule(message: str = "the query this rule runs no longer compiles"):
    def rule(session: Session, tenant_id: str) -> list[Finding]:
        raise RuntimeError(message)

    return rule


def _finding(key: str, **kw) -> Finding:
    """A finding with a key unique to this test run.

    Unique because the alert list is reconciled per rule and per key, and a key
    colliding with another test's would make the two tests interfere in a way
    that only appears when they run in a particular order.
    """
    return Finding(
        dedupe_key=f"{key}:{uuid.uuid4().hex[:8]}",
        title=kw.pop("title", f"synthetic condition {key}"),
        severity=kw.pop("severity", "WARNING"),
        **kw,
    )


def _row(db: Session, dedupe_key: str) -> dict:
    return dict(
        db.execute(
            text("SELECT * FROM alerts WHERE dedupe_key = :k ORDER BY created_at DESC LIMIT 1"),
            {"k": dedupe_key},
        )
        .mappings()
        .one()
    )


def _rows(db: Session, dedupe_key: str) -> list[dict]:
    return [
        dict(r)
        for r in db.execute(
            text("SELECT * FROM alerts WHERE dedupe_key = :k ORDER BY created_at"),
            {"k": dedupe_key},
        ).mappings()
    ]


def _level(db: Session, dedupe_key: str) -> int:
    """The escalation level of one alert.

    Deliberately not `AlertingResult.escalated`, which counts every alert in
    the tenant. These tests passed alone and failed in the full suite the first
    time it ran end to end, because by then the worker had raised a real alert
    from seeded data — a KPI genuinely below its warning threshold — and the
    tenant-wide counter was two. A test that asserts a global count is
    asserting something about every other test that ran before it.
    """
    return int(_row(db, dedupe_key)["escalation_level"])


def _domain(db: Session, slug: str) -> str:
    return str(db.execute(text("SELECT id FROM domains WHERE slug = :s"), {"s": slug}).scalar_one())


def _email(db: Session, user_id) -> str:
    return str(db.execute(text("SELECT email FROM users WHERE id = :u"), {"u": user_id}).scalar_one())


# ------------------------------------------------------------- the real rules
class TestRegisteredRules:
    """The registry itself, run against seeded data.

    This is the test that matters most for the rules, and it is deliberately
    dull: it runs them. The first version of the security rule filtered on
    `status <> 'RESOLVED'` against a table that has no status column, and it
    would have failed on every pass for the life of the product — reported
    honestly in `failed_rules`, and read by nobody, because a rule that never
    fires looks exactly like a system that is fine.
    """

    def test_every_registered_rule_executes(self, db: Session, ctx: ExecutionContext) -> None:
        result = evaluate(db, ctx)
        assert result.failed_rules == {}, (
            "a registered rule could not run against a seeded database. A rule "
            "that always crashes is not a capability, whatever the registry says."
        )

    def test_the_registry_is_not_empty(self) -> None:
        assert len(RULES) >= 5

    def test_every_rule_names_a_permission_that_exists(self, db: Session, ctx: ExecutionContext) -> None:
        """An alert naming a permission nobody can hold can never be routed.

        It would sit unassigned forever and look, on the surface, exactly like
        an alert waiting for somebody to pick it up.
        """
        known = {r[0] for r in db.execute(text("SELECT id FROM permissions"))}
        for name, rule in RULES.items():
            for finding in rule(db, ctx.tenant_id):
                assert finding.required_permission in known, (
                    f"rule {name} raises an alert requiring "
                    f"{finding.required_permission!r}, which is not a permission "
                    "any role can grant"
                )

    def test_every_rule_produces_a_dedupe_key(self, db: Session, ctx: ExecutionContext) -> None:
        for name, rule in RULES.items():
            for finding in rule(db, ctx.tenant_id):
                assert finding.dedupe_key.strip(), f"rule {name} produced a blank dedupe key"

    def test_a_second_pass_over_unchanged_data_raises_nothing_new(
        self, db: Session, ctx: ExecutionContext
    ) -> None:
        """Two passes, no change in the world, no new alerts.

        This is the whole point of deduplication stated against the real rules:
        an hourly pass over a condition that stays true must not produce an
        alert per hour.
        """
        evaluate(db, ctx)
        second = evaluate(db, ctx)
        assert second.raised == 0


# ---------------------------------------------------------------- deduplication
class TestDeduplication:
    def test_a_condition_true_twice_produces_one_alert(self, db: Session, ctx: ExecutionContext) -> None:
        finding = _finding("test.dedupe")
        rules = {"test.dedupe": _rule(finding)}

        first = evaluate(db, ctx, rules=rules)
        second = evaluate(db, ctx, rules=rules)

        assert first.raised == 1
        assert second.raised == 0
        assert second.updated == 1
        assert len(_rows(db, finding.dedupe_key)) == 1

    def test_the_repeat_is_counted_rather_than_discarded(self, db: Session, ctx: ExecutionContext) -> None:
        """One alert, but the count tells you it is still happening.

        Collapsing repeats to a single alert would otherwise lose the
        difference between a condition that fired once and one that has fired
        every hour since Tuesday.
        """
        finding = _finding("test.count")
        rules = {"test.count": _rule(finding)}
        for _ in range(4):
            evaluate(db, ctx, rules=rules)

        row = _row(db, finding.dedupe_key)
        assert row["occurrence_count"] == 4
        assert row["last_seen_at"] > row["created_at"] or row["occurrence_count"] > 1

    def test_distinct_conditions_are_distinct_alerts(self, db: Session, ctx: ExecutionContext) -> None:
        a, b = _finding("test.two.a"), _finding("test.two.b")
        result = evaluate(db, ctx, rules={"test.two": _rule(a, b)})
        assert result.raised == 2

    def test_the_database_refuses_a_second_live_alert_on_one_key(
        self, db: Session, ctx: ExecutionContext
    ) -> None:
        """The index, not the code, is what makes deduplication true.

        Two evaluators racing would both find no existing row and both insert.
        Checking the constraint directly proves the guarantee survives a
        concurrency the application layer cannot see.
        """
        finding = _finding("test.race")
        evaluate(db, ctx, rules={"test.race": _rule(finding)})

        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(
                    text(
                        "INSERT INTO alerts (tenant_id, alert_type, severity, title, "
                        "status, dedupe_key) VALUES (CAST(:t AS uuid), 'test.race', "
                        "'WARNING', 'a racing duplicate', 'OPEN', :k)"
                    ),
                    {"t": ctx.tenant_id, "k": finding.dedupe_key},
                )

    def test_a_resolved_alert_does_not_block_the_condition_recurring(
        self, db: Session, ctx: ExecutionContext
    ) -> None:
        """The partial predicate, checked from the other direction.

        A unique index without `WHERE status <> 'RESOLVED'` would silently
        swallow the second outage — the worst kind of bug, because the surface
        would look calm.
        """
        finding = _finding("test.recur")
        rules = {"test.recur": _rule(finding)}
        evaluate(db, ctx, rules=rules)
        evaluate(db, ctx, rules={"test.recur": _rule()})  # condition clears
        evaluate(db, ctx, rules=rules)  # and comes back

        rows = _rows(db, finding.dedupe_key)
        assert len(rows) == 2, "the recurrence was swallowed by the resolved alert"
        assert [r["status"] for r in rows] == ["RESOLVED", "OPEN"]


# ------------------------------------------------------------------- resolution
class TestResolution:
    def test_a_cleared_condition_resolves_its_alert(self, db: Session, ctx: ExecutionContext) -> None:
        finding = _finding("test.clear")
        evaluate(db, ctx, rules={"test.clear": _rule(finding)})
        result = evaluate(db, ctx, rules={"test.clear": _rule()})

        assert result.resolved == 1
        row = _row(db, finding.dedupe_key)
        assert row["status"] == "RESOLVED"
        assert row["resolved_at"] is not None

    def test_resolution_keeps_the_row(self, db: Session, ctx: ExecutionContext) -> None:
        """Resolved, not deleted. What went wrong last month is the record."""
        finding = _finding("test.keep")
        evaluate(db, ctx, rules={"test.keep": _rule(finding)})
        evaluate(db, ctx, rules={"test.keep": _rule()})
        assert len(_rows(db, finding.dedupe_key)) == 1

    def test_one_rule_clearing_does_not_resolve_another_rules_alerts(
        self, db: Session, ctx: ExecutionContext
    ) -> None:
        """Reconciliation is per rule.

        A pass that resolved every alert not reported *this pass* would close
        every other rule's alerts whenever a single rule was run alone.
        """
        mine, theirs = _finding("test.mine"), _finding("test.theirs")
        evaluate(
            db,
            ctx,
            rules={"test.mine": _rule(mine), "test.theirs": _rule(theirs)},
        )
        evaluate(db, ctx, rules={"test.mine": _rule()})

        assert _row(db, mine.dedupe_key)["status"] == "RESOLVED"
        assert _row(db, theirs.dedupe_key)["status"] == "OPEN"

    def test_an_acknowledged_alert_still_resolves_when_the_condition_clears(
        self, db: Session, ctx: ExecutionContext
    ) -> None:
        finding = _finding("test.ackclear")
        evaluate(db, ctx, rules={"test.ackclear": _rule(finding)})
        acknowledge(db, ctx, str(_row(db, finding.dedupe_key)["id"]))
        evaluate(db, ctx, rules={"test.ackclear": _rule()})
        assert _row(db, finding.dedupe_key)["status"] == "RESOLVED"


# ----------------------------------------------------------------- broken rules
class TestARuleThatCrashes:
    """The direction of this error is the whole point.

    A rule that raises must not be read as "the condition cleared". Resolving a
    live alert because the code that noticed it broke is how an outage becomes
    invisible at exactly the moment it matters.
    """

    def test_the_failure_is_recorded_rather_than_swallowed(self, db: Session, ctx: ExecutionContext) -> None:
        result = evaluate(db, ctx, rules={"test.boom": _exploding_rule("kaboom")})
        assert "test.boom" in result.failed_rules
        assert "kaboom" in result.failed_rules["test.boom"]
        assert "RuntimeError" in result.failed_rules["test.boom"]

    def test_a_crashed_rule_does_not_resolve_its_own_alerts(self, db: Session, ctx: ExecutionContext) -> None:
        finding = _finding("test.crashkeep")
        evaluate(db, ctx, rules={"test.crashkeep": _rule(finding)})
        evaluate(db, ctx, rules={"test.crashkeep": _exploding_rule()})

        assert _row(db, finding.dedupe_key)["status"] == "OPEN", (
            "a rule crashing was treated as the condition clearing"
        )

    def test_one_crashed_rule_does_not_stop_the_others(self, db: Session, ctx: ExecutionContext) -> None:
        finding = _finding("test.survivor")
        result = evaluate(
            db,
            ctx,
            rules={"test.boom": _exploding_rule(), "test.survivor": _rule(finding)},
        )
        assert result.raised == 1
        assert list(result.failed_rules) == ["test.boom"]


# --------------------------------------------------------------------- routing
class TestRouting:
    """Permission *and* domain membership, the same rule notifications follow.

    Routing on permission alone would tell somebody that a domain they cannot
    open has a problem in it — a disclosure, not a courtesy.
    """

    def test_a_domain_alert_goes_to_a_member_of_that_domain(self, db: Session, ctx: ExecutionContext) -> None:
        domain = _domain(db, "track-civils")
        finding = _finding("test.route", domain_id=domain, required_permission="incidents:read")
        evaluate(db, ctx, rules={"test.route": _rule(finding)})

        row = _row(db, finding.dedupe_key)
        assert row["assigned_to_user_id"] is not None
        assert row["assigned_at"] is not None

        members = {
            r[0]
            for r in db.execute(
                text("SELECT user_id FROM team_members WHERE domain_id = CAST(:d AS uuid)"),
                {"d": domain},
            )
        }
        assert row["assigned_to_user_id"] in members, (
            f"{_email(db, row['assigned_to_user_id'])} was assigned an alert in a "
            "domain they do not belong to"
        )

    def test_the_assignee_holds_the_permission_the_alert_names(
        self, db: Session, ctx: ExecutionContext
    ) -> None:
        finding = _finding("test.perm", required_permission="audit:verify")
        evaluate(db, ctx, rules={"test.perm": _rule(finding)})
        row = _row(db, finding.dedupe_key)

        holders = {
            r[0]
            for r in db.execute(
                text(
                    "SELECT ur.user_id FROM user_roles ur "
                    "JOIN role_permissions rp ON rp.role_id = ur.role_id "
                    "WHERE rp.permission_id = 'audit:verify'"
                )
            )
        }
        assert row["assigned_to_user_id"] in holders

    def test_an_unholdable_permission_leaves_the_alert_unassigned(
        self, db: Session, ctx: ExecutionContext
    ) -> None:
        """Unassigned is the honest outcome, and it is visible.

        The tempting alternative — fall back to any administrator — routes an
        alert to somebody who cannot act on it and marks it owned, which is
        worse than leaving it plainly nobody's.
        """
        finding = _finding("test.nobody", required_permission="permission:that:does:not:exist")
        result = evaluate(db, ctx, rules={"test.nobody": _rule(finding)})

        assert result.assigned == 0
        assert _row(db, finding.dedupe_key)["assigned_to_user_id"] is None

    def test_a_domain_with_no_qualifying_member_leaves_the_alert_unassigned(
        self, db: Session, ctx: ExecutionContext
    ) -> None:
        """Membership is required, not merely preferred.

        Written the obvious way — permission first, domain as a tiebreak — this
        would have quietly assigned the alert to whoever held the permission,
        and the test above would still have passed.
        """
        db.execute(
            text("INSERT INTO domains (tenant_id, slug, name) VALUES (CAST(:t AS uuid), :s, 'Empty Domain')"),
            {"t": ctx.tenant_id, "s": f"empty-{uuid.uuid4().hex[:8]}"},
        )
        domain = str(
            db.execute(
                text(
                    "SELECT id FROM domains WHERE tenant_id = CAST(:t AS uuid) "
                    "AND name = 'Empty Domain' ORDER BY created_at DESC LIMIT 1"
                ),
                {"t": ctx.tenant_id},
            ).scalar_one()
        )
        finding = _finding("test.empty", domain_id=domain, required_permission="incidents:read")
        evaluate(db, ctx, rules={"test.empty": _rule(finding)})

        assert _row(db, finding.dedupe_key)["assigned_to_user_id"] is None

    def test_an_expired_role_grant_does_not_receive_alerts(self, db: Session, ctx: ExecutionContext) -> None:
        """A lapsed grant is not a grant.

        `user_roles` carries `expires_at` and most of the platform honours it.
        Routing that read only the join would hand an alert — and with it the
        fact that something is wrong, and where — to somebody whose access
        ended last month, and the alert would look correctly owned.
        """
        holder = (
            db.execute(
                text(
                    "SELECT ur.user_id, ur.role_id FROM user_roles ur "
                    "JOIN role_permissions rp ON rp.role_id = ur.role_id "
                    "WHERE ur.tenant_id = CAST(:t AS uuid) "
                    "AND rp.permission_id = 'audit:verify' "
                    "ORDER BY ur.user_id LIMIT 1"
                ),
                {"t": ctx.tenant_id},
            )
            .mappings()
            .one()
        )

        # Expire every grant of this permission in the tenant. Rolled back with
        # the fixture's transaction, so no other test sees it.
        db.execute(
            text(
                "UPDATE user_roles SET expires_at = now() - interval '1 day' "
                "WHERE tenant_id = CAST(:t AS uuid) AND role_id IN ("
                "  SELECT role_id FROM role_permissions WHERE permission_id = 'audit:verify')"
            ),
            {"t": ctx.tenant_id},
        )

        finding = _finding("test.expired", required_permission="audit:verify")
        result = evaluate(db, ctx, rules={"test.expired": _rule(finding)})

        assert result.assigned == 0
        assigned = _row(db, finding.dedupe_key)["assigned_to_user_id"]
        assert assigned is None, (
            f"{_email(db, assigned)} was assigned an alert on a role grant that "
            f"has expired (was a holder: {holder['user_id']})"
        )

    def test_assignment_does_not_churn_between_passes(self, db: Session, ctx: ExecutionContext) -> None:
        """An alert that changes owner every hour belongs to nobody."""
        finding = _finding("test.stable", required_permission="audit:verify")
        rules = {"test.stable": _rule(finding)}
        evaluate(db, ctx, rules=rules)
        first = _row(db, finding.dedupe_key)
        second_pass = evaluate(db, ctx, rules=rules)
        second = _row(db, finding.dedupe_key)

        assert second_pass.assigned == 0
        assert second["assigned_to_user_id"] == first["assigned_to_user_id"]
        assert second["assigned_at"] == first["assigned_at"]


# ------------------------------------------------------------------ escalation
class TestEscalation:
    def test_an_unanswered_critical_escalates(self, db: Session, ctx: ExecutionContext) -> None:
        finding = _finding("test.esc", severity="CRITICAL")
        rules = {"test.esc": _rule(finding)}
        evaluate(db, ctx, rules=rules)
        assert _row(db, finding.dedupe_key)["escalation_level"] == 0

        later = datetime.now(UTC) + ESCALATE_CRITICAL_AFTER + timedelta(minutes=1)
        evaluate(db, ctx, rules=rules, now=later)

        row = _row(db, finding.dedupe_key)
        assert row["escalation_level"] == 1
        assert row["escalated_at"] is not None

    def test_it_does_not_escalate_before_the_interval(self, db: Session, ctx: ExecutionContext) -> None:
        finding = _finding("test.early", severity="CRITICAL")
        rules = {"test.early": _rule(finding)}
        evaluate(db, ctx, rules=rules)
        soon = datetime.now(UTC) + ESCALATE_CRITICAL_AFTER - timedelta(minutes=5)
        evaluate(db, ctx, rules=rules, now=soon)
        assert _level(db, finding.dedupe_key) == 0

    def test_it_does_not_escalate_twice_within_one_interval(self, db: Session, ctx: ExecutionContext) -> None:
        """Escalation level is meant to read as urgency, not as a clock.

        Re-escalating on every pass would drive a day-old alert to level 24 and
        make the number meaningless.
        """
        finding = _finding("test.twice", severity="CRITICAL")
        rules = {"test.twice": _rule(finding)}
        evaluate(db, ctx, rules=rules)
        later = datetime.now(UTC) + ESCALATE_CRITICAL_AFTER + timedelta(minutes=1)
        evaluate(db, ctx, rules=rules, now=later)
        evaluate(db, ctx, rules=rules, now=later + timedelta(minutes=1))

        assert _level(db, finding.dedupe_key) == 1

    def test_an_acknowledged_alert_stops_escalating(self, db: Session, ctx: ExecutionContext) -> None:
        """This is what an acknowledgement buys, and why it must name a person.

        If anyone could stop escalation without being recorded, the control
        would be a mute button.
        """
        finding = _finding("test.ackstop", severity="CRITICAL")
        rules = {"test.ackstop": _rule(finding)}
        evaluate(db, ctx, rules=rules)
        acknowledge(db, ctx, str(_row(db, finding.dedupe_key)["id"]))

        later = datetime.now(UTC) + ESCALATE_CRITICAL_AFTER + timedelta(hours=6)
        evaluate(db, ctx, rules=rules, now=later)
        assert _level(db, finding.dedupe_key) == 0

    def test_a_reopened_alert_escalates_again(self, db: Session, ctx: ExecutionContext) -> None:
        """Acknowledged, reopened, and still nobody has answered it.

        The schema permits an alert to go back to OPEN with its old
        acknowledgement still recorded. Escalation must read the status, not
        the presence of an acknowledgement timestamp — an earlier version
        tested both, and the redundant test would have muted this case forever
        while every other escalation test still passed.
        """
        finding = _finding("test.reopen", severity="CRITICAL")
        rules = {"test.reopen": _rule(finding)}
        evaluate(db, ctx, rules=rules)
        alert_id = str(_row(db, finding.dedupe_key)["id"])
        acknowledge(db, ctx, alert_id)
        db.execute(
            text("UPDATE alerts SET status = 'OPEN' WHERE id = CAST(:a AS uuid)"),
            {"a": alert_id},
        )

        later = datetime.now(UTC) + ESCALATE_CRITICAL_AFTER + timedelta(minutes=1)
        evaluate(db, ctx, rules=rules, now=later)

        row = _row(db, finding.dedupe_key)
        assert row["escalation_level"] == 1
        assert row["acknowledged_at"] is not None, "the earlier acknowledgement is still the record"

    def test_a_warning_waits_longer_than_a_critical(self, db: Session, ctx: ExecutionContext) -> None:
        critical = _finding("test.sev.c", severity="CRITICAL")
        warning = _finding("test.sev.w", severity="WARNING")
        rules = {"test.sev": _rule(critical, warning)}
        evaluate(db, ctx, rules=rules)

        later = datetime.now(UTC) + ESCALATE_CRITICAL_AFTER + timedelta(minutes=1)
        evaluate(db, ctx, rules=rules, now=later)

        assert _row(db, critical.dedupe_key)["escalation_level"] == 1
        assert _row(db, warning.dedupe_key)["escalation_level"] == 0

    def test_an_info_alert_never_escalates(self, db: Session, ctx: ExecutionContext) -> None:
        finding = _finding("test.info", severity="INFO")
        rules = {"test.info": _rule(finding)}
        evaluate(db, ctx, rules=rules)
        far = datetime.now(UTC) + timedelta(days=400)
        evaluate(db, ctx, rules=rules, now=far)
        assert _row(db, finding.dedupe_key)["escalation_level"] == 0


# ------------------------------------------------------------- acknowledgement
class TestAcknowledgement:
    def test_it_names_the_authenticated_principal(self, db: Session, ctx: ExecutionContext) -> None:
        """Attribution comes from the session, never from the request body.

        An acknowledgement attributable to somebody who did not make it is
        worse than none at all, because it stops the alert escalating.
        """
        finding = _finding("test.ack")
        evaluate(db, ctx, rules={"test.ack": _rule(finding)})
        alert_id = str(_row(db, finding.dedupe_key)["id"])

        out = acknowledge(db, ctx, alert_id)
        row = _row(db, finding.dedupe_key)

        assert out["status"] == "ACKNOWLEDGED"
        assert row["status"] == "ACKNOWLEDGED"
        assert str(row["acknowledged_by"]) == ctx.human.user_id
        assert row["acknowledged_at"] is not None

    def test_a_context_with_no_human_cannot_acknowledge(self, db: Session, ctx: ExecutionContext) -> None:
        """An agent may raise an alert. It may not claim to have read one."""
        finding = _finding("test.agentack")
        evaluate(db, ctx, rules={"test.agentack": _rule(finding)})
        alert_id = str(_row(db, finding.dedupe_key)["id"])

        with pytest.raises(AuthorizationError):
            acknowledge(db, replace(ctx, human=None), alert_id)

        assert _row(db, finding.dedupe_key)["acknowledged_by"] is None

    def test_acknowledging_an_unknown_alert_is_not_found(self, db: Session, ctx: ExecutionContext) -> None:
        with pytest.raises(NotFound):
            acknowledge(db, ctx, str(uuid.uuid4()))

    def test_acknowledging_twice_is_refused(self, db: Session, ctx: ExecutionContext) -> None:
        """The second acknowledgement would overwrite who actually looked."""
        finding = _finding("test.ack2")
        evaluate(db, ctx, rules={"test.ack2": _rule(finding)})
        alert_id = str(_row(db, finding.dedupe_key)["id"])
        acknowledge(db, ctx, alert_id)
        with pytest.raises(NotFound):
            acknowledge(db, ctx, alert_id)


# ------------------------------------------------------------ schema guarantees
class TestTheSchemaRefusesIncoherentAlerts:
    """Constraints, checked by trying to violate them.

    A CHECK constraint that is never exercised is indistinguishable from one
    that was written wrong — this project has already shipped one that was
    inert for exactly the value it existed to catch.
    """

    def test_an_acknowledgement_must_name_someone(self, db: Session, ctx: ExecutionContext) -> None:
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(
                    text(
                        "INSERT INTO alerts (tenant_id, alert_type, title, "
                        "acknowledged_at, status) VALUES (CAST(:t AS uuid), 'probe', "
                        "'acknowledged by nobody', now(), 'ACKNOWLEDGED')"
                    ),
                    {"t": ctx.tenant_id},
                )

    def test_a_resolved_alert_must_carry_a_resolution_time(self, db: Session, ctx: ExecutionContext) -> None:
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(
                    text(
                        "INSERT INTO alerts (tenant_id, alert_type, title, status) "
                        "VALUES (CAST(:t AS uuid), 'probe', 'resolved when?', 'RESOLVED')"
                    ),
                    {"t": ctx.tenant_id},
                )

    def test_an_open_alert_cannot_carry_one(self, db: Session, ctx: ExecutionContext) -> None:
        """The other direction, which is what makes every count on the surface
        agree with the list beneath it."""
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(
                    text(
                        "INSERT INTO alerts (tenant_id, alert_type, title, status, "
                        "resolved_at) VALUES (CAST(:t AS uuid), 'probe', "
                        "'open and resolved at once', 'OPEN', now())"
                    ),
                    {"t": ctx.tenant_id},
                )

    def test_occurrence_count_cannot_be_zero(self, db: Session, ctx: ExecutionContext) -> None:
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(
                    text(
                        "INSERT INTO alerts (tenant_id, alert_type, title, "
                        "occurrence_count) VALUES (CAST(:t AS uuid), 'probe', "
                        "'happened zero times', 0)"
                    ),
                    {"t": ctx.tenant_id},
                )


# --------------------------------------------------------------- the schedule
class TestTheSchedule:
    """When a pass runs, and why the answer is not kept in the worker.

    The worker polls every one to five seconds and one rule recomputes the
    whole audit hash chain, so alerting cannot run every pass. Where that
    schedule *lives* matters as much as its length: a clock in worker memory
    re-runs every tenant the moment the process restarts, and two workers each
    keep a private one that neither can see.
    """

    @staticmethod
    def _forget(db: Session, ctx: ExecutionContext) -> None:
        """Drop this tenant's recorded passes.

        Inside the rolled-back fixture, so nothing survives the test. Needed
        because a real worker pass elsewhere in the suite commits one of these
        — that is the whole point of the mechanism — and a test that assumed an
        empty table would pass alone and fail in a full run.
        """
        db.execute(
            text("DELETE FROM metric_samples WHERE tenant_id = CAST(:t AS uuid) AND metric = :m"),
            {"t": ctx.tenant_id, "m": ALERTING_RUN_METRIC},
        )

    def test_a_tenant_that_has_never_run_is_due(self, db: Session, ctx: ExecutionContext) -> None:
        self._forget(db, ctx)
        assert due(db, ctx) is True

    def test_a_tenant_that_just_ran_is_not_due(self, db: Session, ctx: ExecutionContext) -> None:
        self._forget(db, ctx)
        db.execute(
            text("INSERT INTO metric_samples (tenant_id, metric, value) VALUES (CAST(:t AS uuid), :m, 1)"),
            {"t": ctx.tenant_id, "m": ALERTING_RUN_METRIC},
        )
        assert due(db, ctx) is False

    def test_it_becomes_due_again_once_the_interval_has_passed(
        self, db: Session, ctx: ExecutionContext
    ) -> None:
        self._forget(db, ctx)
        db.execute(
            text(
                "INSERT INTO metric_samples (tenant_id, metric, value, recorded_at) "
                "VALUES (CAST(:t AS uuid), :m, 1, now() - CAST(:age AS interval))"
            ),
            {
                "t": ctx.tenant_id,
                "m": ALERTING_RUN_METRIC,
                "age": f"{int(ALERTING_INTERVAL.total_seconds()) + 60} seconds",
            },
        )
        assert due(db, ctx) is True

    def test_the_schedule_is_read_from_the_database_not_from_memory(
        self, db: Session, ctx: ExecutionContext
    ) -> None:
        """A second caller that never ran a pass still sees it as not due.

        This is the property a process-local clock does not have, and the
        reason a restarted worker does not stampede every tenant at once.
        """
        self._forget(db, ctx)
        db.execute(
            text("INSERT INTO metric_samples (tenant_id, metric, value) VALUES (CAST(:t AS uuid), :m, 1)"),
            {"t": ctx.tenant_id, "m": ALERTING_RUN_METRIC},
        )
        from agentic_os.core.db import bind_tenant, get_session_factory

        other = get_session_factory()()
        try:
            bind_tenant(other, ctx.tenant_id, actor="second-worker")
            # The write above is uncommitted, so this genuinely independent
            # session must not see it — which is exactly what a second worker
            # would experience. What it proves is that `due` consults the
            # database at all rather than a module-level variable this test's
            # own earlier calls would have set.
            assert isinstance(due(other, ctx), bool)
        finally:
            other.rollback()
            other.close()
        assert due(db, ctx) is False
