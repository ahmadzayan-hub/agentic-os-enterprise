"""The alert API, exercised through the real application.

The engine's own tests prove that alerts are raised, deduplicated and routed.
These prove the second half, which is a security property rather than an
operational one: an alert is a disclosure — it says something is wrong and
where — so the list must be confined to what the caller may see *before*
retrieval, not filtered after it.

The target the brief sets is unauthorized cross-domain data access of zero, and
the counts are part of that. A total taken outside the caller's boundary would
report how many alerts exist that they cannot see, which leaks the fact while
withholding only the wording.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from agentic_os.api.app import create_app
from agentic_os.core.db import provisioning_session_scope
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

ENGINEER = "field.engineer@rta.example"  # signalling only; no audit:verify
SIGNALLING_LEAD = "systems.lead@rta.example"
ROLLING_STOCK_LEAD = "rollingstock.lead@rta.example"
AUDITOR = "auditor@rta.example"  # cross-domain, holds audit:verify

#: Every alert this module creates carries this prefix so teardown can remove
#: exactly its own rows. Committed rows are the price of testing through HTTP,
#: and a suite that leaves them behind poisons every later run's counts.
PREFIX = "apitest"


@pytest.fixture(scope="module", autouse=True)
def _no_residue(seeded, tenant_id: str) -> Iterator[None]:
    """Remove every alert this module causes to exist.

    Not only the ones it inserts: `/alerts/evaluate` runs the real rules and
    commits whatever they find, so a test that merely calls it leaves rows
    behind. Test rows in `alerts` are worse than untidy — they are counted by
    the operations surface and by these tests' own totals, so a later run would
    be measuring the residue of an earlier one.
    """
    with provisioning_session_scope() as session:
        before = {
            str(r)
            for r in session.execute(
                text("SELECT id FROM alerts WHERE tenant_id = CAST(:t AS uuid)"),
                {"t": tenant_id},
            ).scalars()
        }
    try:
        yield
    finally:
        with provisioning_session_scope() as session:
            session.execute(
                text(
                    "DELETE FROM alerts WHERE tenant_id = CAST(:t AS uuid) "
                    "AND NOT (CAST(id AS text) = ANY(CAST(:keep AS text[])))"
                ),
                {"t": tenant_id, "keep": sorted(before)},
            )


@pytest.fixture(scope="module")
def client(seeded) -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture(scope="module")
def alerts(seeded, tenant_id: str) -> Iterator[dict[str, str]]:
    """Three alerts spanning the two axes of the boundary.

    Committed, because the API runs in its own sessions and cannot see an
    uncommitted transaction. Removed again on teardown by dedupe-key prefix.
    """
    run = uuid.uuid4().hex[:8]
    created: dict[str, str] = {}
    with provisioning_session_scope() as session:
        domains = {
            str(r["slug"]): str(r["id"])
            for r in session.execute(
                text("SELECT id, slug FROM domains WHERE tenant_id = CAST(:t AS uuid)"),
                {"t": tenant_id},
            ).mappings()
        }
        for label, domain, permission, severity in (
            ("open", None, "incidents:read", "WARNING"),
            ("rollingstock", domains["rolling-stock"], "incidents:read", "CRITICAL"),
            ("privileged", None, "audit:verify", "CRITICAL"),
        ):
            key = f"{PREFIX}.{run}.{label}"
            alert_id = session.execute(
                text(
                    "INSERT INTO alerts (tenant_id, alert_type, severity, title, status, "
                    "dedupe_key, domain_id, required_permission, source) "
                    "VALUES (CAST(:t AS uuid), 'apitest', :sev, :title, 'OPEN', :k, "
                    "CAST(NULLIF(:d, '') AS uuid), :p, 'test') RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "sev": severity,
                    "title": f"api test alert {label}",
                    "k": key,
                    "d": domain or "",
                    "p": permission,
                },
            ).scalar_one()
            created[label] = str(alert_id)
    try:
        yield created
    finally:
        with provisioning_session_scope() as session:
            session.execute(text("DELETE FROM alerts WHERE dedupe_key LIKE :p"), {"p": f"{PREFIX}.%"})


def _ids(payload: dict) -> set[str]:
    return {a["id"] for a in payload["alerts"]}


# ------------------------------------------------------------- authentication
def test_the_list_refuses_an_anonymous_caller(client: TestClient) -> None:
    assert client.get("/api/v1/alerts").status_code == 401


def test_acknowledgement_refuses_an_anonymous_caller(client: TestClient) -> None:
    """Refused before the identifier is looked up, so it confirms nothing."""
    path = f"/api/v1/alerts/{uuid.uuid4()}/acknowledge"
    assert client.post(path).status_code == 401


def test_running_a_pass_refuses_an_anonymous_caller(client: TestClient) -> None:
    assert client.post("/api/v1/alerts/evaluate").status_code == 401


# -------------------------------------------------------------- the boundary
def test_a_member_sees_an_alert_in_their_own_domain(client: TestClient, sign_in, alerts: dict) -> None:
    body = client.get("/api/v1/alerts", headers=sign_in(ROLLING_STOCK_LEAD)).json()
    assert alerts["rollingstock"] in _ids(body)


def test_a_cross_domain_alert_is_not_returned(client: TestClient, sign_in, alerts: dict) -> None:
    """The signalling engineer must not learn that rolling stock has a problem."""
    body = client.get("/api/v1/alerts", headers=sign_in(ENGINEER)).json()
    assert alerts["rollingstock"] not in _ids(body)
    assert alerts["open"] in _ids(body), "an unscoped alert is still theirs to see"


def test_an_alert_requiring_a_permission_the_caller_lacks_is_not_returned(
    client: TestClient, sign_in, alerts: dict
) -> None:
    """Domain membership is not the only axis.

    This alert has no domain at all, so a boundary written on domains alone
    would return it — and it names `audit:verify`, which this caller cannot
    hold.
    """
    body = client.get("/api/v1/alerts", headers=sign_in(ENGINEER)).json()
    assert alerts["privileged"] not in _ids(body)


def test_the_privileged_alert_reaches_somebody_who_can_act_on_it(
    client: TestClient, sign_in, alerts: dict
) -> None:
    """The other half of the previous test.

    Without it, a boundary that returned nothing to anyone would pass.
    """
    body = client.get("/api/v1/alerts", headers=sign_in(AUDITOR)).json()
    assert alerts["privileged"] in _ids(body)


def test_an_oversight_role_sees_across_domains(client: TestClient, sign_in, alerts: dict) -> None:
    body = client.get("/api/v1/alerts", headers=sign_in(AUDITOR)).json()
    assert alerts["rollingstock"] in _ids(body)


def test_two_leads_do_not_see_each_others_domain_alerts(client: TestClient, sign_in, alerts: dict) -> None:
    signalling = _ids(client.get("/api/v1/alerts", headers=sign_in(SIGNALLING_LEAD)).json())
    rolling = _ids(client.get("/api/v1/alerts", headers=sign_in(ROLLING_STOCK_LEAD)).json())
    assert alerts["rollingstock"] in rolling
    assert alerts["rollingstock"] not in signalling


# ------------------------------------------------------------------- counting
def test_the_counts_are_taken_under_the_same_boundary_as_the_list(
    client: TestClient, sign_in, alerts: dict
) -> None:
    """A count is a disclosure too.

    "You have 3 alerts" beside a list of 1 tells the reader two exist that they
    may not see — the exact fact the boundary exists to withhold. Written the
    obvious way, with a separate unfiltered `SELECT count(*)`, this is the bug
    that ships.
    """
    for email in (ENGINEER, SIGNALLING_LEAD, ROLLING_STOCK_LEAD, AUDITOR):
        body = client.get("/api/v1/alerts", headers=sign_in(email), params={"limit": 200}).json()
        assert body["counts"]["total"] == len(body["alerts"]), (
            f"{email} was told a total that does not match what they can see"
        )


def test_the_engineers_total_is_smaller_than_the_auditors(client: TestClient, sign_in, alerts: dict) -> None:
    """Proves the previous test is not passing because nothing is filtered."""
    engineer = client.get("/api/v1/alerts", headers=sign_in(ENGINEER)).json()
    auditor = client.get("/api/v1/alerts", headers=sign_in(AUDITOR)).json()
    assert engineer["counts"]["total"] < auditor["counts"]["total"]


# ---------------------------------------------------- the other reader of alerts
def test_the_incidents_surface_applies_the_same_boundary(client: TestClient, sign_in, alerts: dict) -> None:
    """`/v1/incidents` reads the alerts table too.

    It returned every alert in the tenant, unfiltered. That was invisible for
    as long as nothing raised one, and became a cross-domain disclosure the
    moment something did — the risk of building a boundary for a new route
    while an older route reads the same table beside it.
    """
    body = client.get("/api/v1/incidents", headers=sign_in(ENGINEER)).json()
    titles = {a["title"] for a in body["alerts"]}
    assert "api test alert rollingstock" not in titles, "another domain's alert leaked"
    assert "api test alert privileged" not in titles, "a privileged alert leaked"
    assert "api test alert open" in titles, "the caller's own alert is missing"


def test_the_incidents_surface_still_shows_an_oversight_role_everything(
    client: TestClient, sign_in, alerts: dict
) -> None:
    body = client.get("/api/v1/incidents", headers=sign_in(AUDITOR)).json()
    titles = {a["title"] for a in body["alerts"]}
    assert "api test alert rollingstock" in titles


# ------------------------------------------------------------------ filtering
def test_status_and_severity_filters_narrow_the_list(client: TestClient, sign_in, alerts: dict) -> None:
    headers = sign_in(AUDITOR)
    critical = client.get(
        "/api/v1/alerts", headers=headers, params={"severity": "CRITICAL", "limit": 200}
    ).json()
    assert all(a["severity"] == "CRITICAL" for a in critical["alerts"])
    assert alerts["privileged"] in _ids(critical)

    resolved = client.get(
        "/api/v1/alerts", headers=headers, params={"status": "RESOLVED", "limit": 200}
    ).json()
    assert alerts["open"] not in _ids(resolved)


def test_an_unknown_status_is_rejected_rather_than_ignored(client: TestClient, sign_in) -> None:
    """A filter silently ignored returns more than the caller asked for."""
    response = client.get("/api/v1/alerts", headers=sign_in(AUDITOR), params={"status": "MAYBE"})
    assert response.status_code == 422


def test_pagination_is_bounded(client: TestClient, sign_in) -> None:
    headers = sign_in(AUDITOR)
    assert client.get("/api/v1/alerts", headers=headers, params={"limit": 5000}).status_code == 422
    assert client.get("/api/v1/alerts", headers=headers, params={"offset": -1}).status_code == 422
    body = client.get("/api/v1/alerts", headers=headers, params={"limit": 1}).json()
    assert len(body["alerts"]) <= 1
    assert body["limit"] == 1


def test_the_open_list_is_ordered_with_the_worst_first(client: TestClient, sign_in, alerts: dict) -> None:
    """An operator reads from the top. The ordering is the triage."""
    body = client.get(
        "/api/v1/alerts", headers=sign_in(AUDITOR), params={"status": "OPEN", "limit": 200}
    ).json()
    rank = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    severities = [rank[a["severity"]] for a in body["alerts"]]
    assert severities == sorted(severities)


# ------------------------------------------------------------ acknowledgement
def test_acknowledging_an_invisible_alert_is_not_found(client: TestClient, sign_in, alerts: dict) -> None:
    """404, not 403.

    403 on a specific identifier confirms the identifier is real, and here it
    would also confirm that an alert exists in a domain the caller cannot open.
    The alert must also be left untouched — acknowledging it would stop its
    escalation, which is a denial of service against whoever should have seen
    it.
    """
    response = client.post(f"/api/v1/alerts/{alerts['rollingstock']}/acknowledge", headers=sign_in(ENGINEER))
    assert response.status_code == 404

    with provisioning_session_scope() as session:
        row = (
            session.execute(
                text("SELECT status, acknowledged_by FROM alerts WHERE id = CAST(:a AS uuid)"),
                {"a": alerts["rollingstock"]},
            )
            .mappings()
            .one()
        )
    assert row["status"] == "OPEN"
    assert row["acknowledged_by"] is None


def test_acknowledging_a_visible_alert_records_the_caller(client: TestClient, sign_in, alerts: dict) -> None:
    response = client.post(f"/api/v1/alerts/{alerts['open']}/acknowledge", headers=sign_in(ENGINEER))
    assert response.status_code == 200
    assert response.json()["status"] == "ACKNOWLEDGED"

    with provisioning_session_scope() as session:
        row = (
            session.execute(
                text(
                    "SELECT a.status, u.email FROM alerts a JOIN users u "
                    "ON u.id = a.acknowledged_by WHERE a.id = CAST(:a AS uuid)"
                ),
                {"a": alerts["open"]},
            )
            .mappings()
            .one()
        )
    assert row["status"] == "ACKNOWLEDGED"
    assert row["email"] == ENGINEER, "the acknowledgement names somebody other than the caller"


def test_acknowledging_an_unknown_identifier_is_not_found(client: TestClient, sign_in) -> None:
    response = client.post(f"/api/v1/alerts/{uuid.uuid4()}/acknowledge", headers=sign_in(SIGNALLING_LEAD))
    assert response.status_code == 404


def test_an_oversight_role_cannot_acknowledge_or_trigger(client: TestClient, sign_in, alerts: dict) -> None:
    """Reading everything and changing nothing is what an auditor is for.

    The auditor sees across every domain — the widest read in the platform — and
    holds no write. Granting acknowledgement alongside that visibility would let
    the one role that can see every alert also silence every alert.
    """
    headers = sign_in(AUDITOR)
    assert client.post("/api/v1/alerts/evaluate", headers=headers).status_code == 403
    path = f"/api/v1/alerts/{alerts['open']}/acknowledge"
    assert client.post(path, headers=headers).status_code == 403


# --------------------------------------------------------------- running a pass
def test_a_pass_reports_which_rules_ran_and_which_failed(client: TestClient, sign_in) -> None:
    """`failed_rules` is returned rather than logged.

    A rule that stopped compiling is the failure mode that hides best: the pass
    still succeeds, the list still renders, and nothing is ever raised again.
    Putting it in the response makes it something a caller has to look at.
    """
    response = client.post("/api/v1/alerts/evaluate", headers=sign_in(SIGNALLING_LEAD))
    assert response.status_code == 200
    body = response.json()
    assert body["registered_rules"], "a pass that runs no rules is not a pass"
    assert body["failed_rules"] == {}, f"a registered rule failed: {body['failed_rules']}"
    for field in ("raised", "updated", "resolved", "assigned", "escalated"):
        assert isinstance(body[field], int)
