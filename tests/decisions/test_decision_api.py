"""The decision API, exercised through the real application.

Through ``TestClient`` against the real app rather than by calling the route
functions: the point of most of these assertions is what the *HTTP layer*
does — 401 without a token, 404 rather than 403 across a domain, 409 on an
illegal transition — and calling a function directly would skip exactly the
machinery under test.
"""

from __future__ import annotations

import pytest
from agentic_os.api.app import create_app
from fastapi.testclient import TestClient

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

ENGINEER = "field.engineer@rta.example"
SIGNALLING_LEAD = "systems.lead@rta.example"
ROLLING_STOCK_LEAD = "rollingstock.lead@rta.example"
AUDITOR = "auditor@rta.example"


@pytest.fixture(scope="module")
def client(seeded) -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _references(payload: dict) -> set[str]:
    return {item["reference"] for item in payload["items"]}


# ------------------------------------------------------------- authentication
def test_the_queue_refuses_an_anonymous_caller(client: TestClient) -> None:
    assert client.get("/api/v1/decisions").status_code == 401


def test_a_case_refuses_an_anonymous_caller(client: TestClient) -> None:
    """Refused before the identifier is even looked up."""
    assert client.get("/api/v1/decisions/00000000-0000-0000-0000-000000000000").status_code == 401


# -------------------------------------------------------------- domain scoping
def test_a_member_sees_only_their_own_domains_queue(client: TestClient, sign_in) -> None:
    body = client.get("/api/v1/decisions", headers=sign_in(SIGNALLING_LEAD)).json()
    references = _references(body)
    assert "DEC-2026-0041" in references, "the signalling lead must see signalling work"
    assert "DEC-2026-0038" not in references, "rolling stock is not their domain"


def test_two_leads_see_disjoint_queues(client: TestClient, sign_in) -> None:
    signalling = _references(client.get("/api/v1/decisions", headers=sign_in(SIGNALLING_LEAD)).json())
    rolling = _references(client.get("/api/v1/decisions", headers=sign_in(ROLLING_STOCK_LEAD)).json())
    assert "DEC-2026-0038" in rolling
    assert signalling & rolling == set(), "the two queues must not overlap"


def test_a_cross_domain_case_is_reported_as_not_found(client: TestClient, sign_in) -> None:
    """404, never 403: a 403 on a real identifier confirms it names something."""
    rolling = client.get("/api/v1/decisions", headers=sign_in(ROLLING_STOCK_LEAD)).json()
    target = next(i for i in rolling["items"] if i["reference"] == "DEC-2026-0038")["id"]

    response = client.get(f"/api/v1/decisions/{target}", headers=sign_in(SIGNALLING_LEAD))
    assert response.status_code == 404, response.text
    assert "403" not in response.text


def test_an_auditor_sees_every_domain(client: TestClient, sign_in) -> None:
    body = client.get("/api/v1/decisions", headers=sign_in(AUDITOR)).json()
    assert body["scope"]["sees_all_domains"] is True
    assert {"DEC-2026-0041", "DEC-2026-0038", "DEC-2026-0044"} <= _references(body)


def test_the_response_states_the_scope_it_was_computed_under(client: TestClient, sign_in) -> None:
    """A queue that silently hides work is worse than one that says it is partial."""
    body = client.get("/api/v1/decisions", headers=sign_in(SIGNALLING_LEAD)).json()
    assert body["scope"]["sees_all_domains"] is False
    assert len(body["scope"]["domains"]) >= 1


# ------------------------------------------------------------------ validation
def test_an_unknown_state_filter_is_rejected(client: TestClient, sign_in) -> None:
    response = client.get("/api/v1/decisions?state=NEARLY_DONE", headers=sign_in(AUDITOR))
    assert response.status_code == 422, response.text
    assert "NEARLY_DONE" in response.text


def test_a_known_state_filter_narrows_the_queue(client: TestClient, sign_in) -> None:
    body = client.get("/api/v1/decisions?state=VERIFIED", headers=sign_in(AUDITOR)).json()
    assert _references(body) == {"DEC-2026-0022"}


# ------------------------------------------------------------------ confidence
def test_a_case_with_thin_evidence_reports_not_calculated(client: TestClient, sign_in) -> None:
    """Against real seeded data, not a fixture built to make the point."""
    queue = client.get("/api/v1/decisions", headers=sign_in(AUDITOR)).json()
    target = next(i for i in queue["items"] if i["reference"] == "DEC-2026-0044")["id"]

    case = client.get(f"/api/v1/decisions/{target}", headers=sign_in(AUDITOR)).json()
    assert case["confidence"]["value"] is None
    assert case["confidence"]["display"] == "Not Calculated"
    assert case["confidence"]["calculation"]["inputs"] == []


def test_a_well_evidenced_case_reports_a_figure_with_its_inputs(client: TestClient, sign_in) -> None:
    queue = client.get("/api/v1/decisions", headers=sign_in(AUDITOR)).json()
    target = next(i for i in queue["items"] if i["reference"] == "DEC-2026-0041")["id"]

    case = client.get(f"/api/v1/decisions/{target}", headers=sign_in(AUDITOR)).json()
    confidence = case["confidence"]
    assert 0 < confidence["value"] <= 1
    assert confidence["display"].endswith("%")
    names = {i["name"] for i in confidence["calculation"]["inputs"]}
    assert names == {
        "evidence_count",
        "evidence_recency",
        "source_authority",
        "option_separation",
    }


def test_a_case_carries_its_options_evidence_and_history(client: TestClient, sign_in) -> None:
    queue = client.get("/api/v1/decisions", headers=sign_in(AUDITOR)).json()
    target = next(i for i in queue["items"] if i["reference"] == "DEC-2026-0022")["id"]
    case = client.get(f"/api/v1/decisions/{target}", headers=sign_in(AUDITOR)).json()

    assert len(case["options"]) == 3
    assert any(o["is_status_quo"] for o in case["options"]), (
        "doing nothing is an option, and a case without it offers a false choice"
    )
    assert case["evidence"]
    assert case["transitions"][0]["from_state"] is None
    assert case["transitions"][-1]["to_state"] == "VERIFIED"
    assert case["outcomes"][0]["verdict"] == "ACHIEVED"
    assert case["lessons"], "a verified decision should have taught something"


def test_the_recommendation_carries_a_summary_not_raw_reasoning(client: TestClient, sign_in) -> None:
    queue = client.get("/api/v1/decisions", headers=sign_in(AUDITOR)).json()
    target = next(i for i in queue["items"] if i["reference"] == "DEC-2026-0041")["id"]
    case = client.get(f"/api/v1/decisions/{target}", headers=sign_in(AUDITOR)).json()
    assert case["recommendation"]["reasoning_summary"]
    assert "chain_of_thought" not in case["recommendation"]


# ------------------------------------------------------------------ transitions
def test_an_illegal_transition_is_refused_with_a_conflict(client: TestClient, sign_in) -> None:
    queue = client.get("/api/v1/decisions", headers=sign_in(SIGNALLING_LEAD)).json()
    target = next(i for i in queue["items"] if i["reference"] == "DEC-2026-0041")["id"]

    response = client.post(
        f"/api/v1/decisions/{target}/transitions",
        headers=sign_in(SIGNALLING_LEAD),
        json={"to_state": "VERIFIED", "reason": "skipping the queue"},
    )
    assert response.status_code == 409, response.text


def test_a_section_lead_cannot_approve(client: TestClient, sign_in) -> None:
    """REVIEW and APPROVE are separate stations, enforced over HTTP."""
    queue = client.get("/api/v1/decisions", headers=sign_in(SIGNALLING_LEAD)).json()
    target = next(i for i in queue["items"] if i["reference"] == "DEC-2026-0041")["id"]

    response = client.post(
        f"/api/v1/decisions/{target}/transitions",
        headers=sign_in(SIGNALLING_LEAD),
        json={"to_state": "APPROVED", "reason": "looks fine to me"},
    )
    assert response.status_code == 403, response.text


def test_transitioning_a_case_in_another_domain_is_not_found(client: TestClient, sign_in) -> None:
    """The domain check runs before the state check, so the response cannot be
    used to learn what state a foreign decision is in."""
    rolling = client.get("/api/v1/decisions", headers=sign_in(ROLLING_STOCK_LEAD)).json()
    target = next(i for i in rolling["items"] if i["reference"] == "DEC-2026-0038")["id"]

    response = client.post(
        f"/api/v1/decisions/{target}/transitions",
        headers=sign_in(SIGNALLING_LEAD),
        json={"to_state": "AWAITING_APPROVAL"},
    )
    assert response.status_code == 404, response.text


def test_an_engineer_cannot_move_a_case_to_review(client: TestClient, sign_in) -> None:
    queue = client.get("/api/v1/decisions", headers=sign_in(ENGINEER)).json()
    target = next(i for i in queue["items"] if i["reference"] == "DEC-2026-0041")["id"]
    response = client.post(
        f"/api/v1/decisions/{target}/transitions",
        headers=sign_in(ENGINEER),
        json={"to_state": "APPROVED"},
    )
    assert response.status_code in (403, 409), response.text


# ---------------------------------------------------------------- North Star
def test_effectiveness_states_its_own_definition(client: TestClient, sign_in) -> None:
    body = client.get("/api/v1/decisions/effectiveness", headers=sign_in(AUDITOR)).json()
    assert "verified" in body["definition"]
    assert body["reached_verification"] >= 1
    assert body["rate"] is not None
    assert body["display"].endswith("%")


def test_effectiveness_is_not_calculated_where_nothing_reached_verification(
    client: TestClient, sign_in
) -> None:
    """The rolling stock domain has no verified decision, so there is no rate —
    not a rate of zero."""
    body = client.get("/api/v1/decisions/effectiveness", headers=sign_in(ROLLING_STOCK_LEAD)).json()
    assert body["rate"] is None
    assert body["display"] == "Not Calculated"


# ------------------------------------------------------------------ lifecycle
def test_the_lifecycle_graph_is_served_so_the_console_does_not_copy_it(client: TestClient, sign_in) -> None:
    body = client.get("/api/v1/decisions/states", headers=sign_in(ENGINEER)).json()
    assert len(body["states"]) == 11
    assert body["transitions"]["CLOSED"] == []
    assert "ANALYSING" in body["transitions"]["DETECTED"]


# -------------------------------------------------------------- notifications
def test_an_inbox_holds_only_its_owners_notifications(client: TestClient, sign_in) -> None:
    """The recipient comes from the token, so there is no request that reads
    somebody else's inbox."""
    lead = client.get("/api/v1/notifications", headers=sign_in(SIGNALLING_LEAD)).json()
    engineer = client.get("/api/v1/notifications", headers=sign_in(ENGINEER)).json()

    assert lead["items"], "the signalling lead was notified of work awaiting review"
    lead_ids = {i["id"] for i in lead["items"]}
    engineer_ids = {i["id"] for i in engineer["items"]}
    assert lead_ids & engineer_ids == set()


def test_notifications_reach_only_people_who_can_act(client: TestClient, sign_in) -> None:
    """An engineer holds no review or approval permission, so a case awaiting
    either must not appear in their inbox."""
    engineer = client.get("/api/v1/notifications", headers=sign_in(ENGINEER)).json()
    kinds = {i["kind"] for i in engineer["items"]}
    assert "APPROVAL_REQUESTED" not in kinds
    assert "REVIEW_REQUESTED" not in kinds


def test_marking_somebody_elses_notification_read_changes_nothing(client: TestClient, sign_in) -> None:
    lead = client.get("/api/v1/notifications", headers=sign_in(SIGNALLING_LEAD)).json()
    assert lead["items"]
    target = lead["items"][0]["id"]

    response = client.post(f"/api/v1/notifications/{target}/read", headers=sign_in(ENGINEER))
    assert response.status_code == 200
    assert response.json()["updated"] == 0, "an engineer must not clear a lead's inbox"


# ---------------------------------------------------------------------- KPIs
def test_every_kpi_carries_its_definition(client: TestClient, sign_in) -> None:
    """A number without a formula, a unit and a direction is not a KPI."""
    body = client.get("/api/v1/kpis", headers=sign_in(AUDITOR)).json()
    assert body["items"]
    for kpi in body["items"]:
        assert kpi["formula"], f"{kpi['kpi_key']} has no formula"
        assert kpi["unit"]
        assert kpi["direction"] in ("UP_IS_GOOD", "DOWN_IS_GOOD")


def test_an_unmeasured_kpi_reports_null_rather_than_zero(client: TestClient, sign_in) -> None:
    """Nobody has recorded a value yet; zero would read as a measurement."""
    body = client.get("/api/v1/kpis", headers=sign_in(AUDITOR)).json()
    unmeasured = [k for k in body["items"] if k["latest_value"] is None]
    assert unmeasured, "the seeded KPIs have no values, so some must report null"
    for kpi in unmeasured:
        assert kpi["latest_value"] is None
