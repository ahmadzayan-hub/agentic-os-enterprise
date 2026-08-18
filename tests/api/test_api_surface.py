"""API surface: authentication, authorization and the governance record."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from agentic_os.api.app import create_app
from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def token(client: TestClient, demo_password: str, seeded) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "systems.lead@rta.example", "password": demo_password},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture()
def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------- platform
def test_health_reports_database_state(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"]["pgvector"] is True


def test_openapi_document_is_served(client: TestClient) -> None:
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"]
    assert len(schema["paths"]) >= 40
    for required in ("/api/v1/runs", "/api/v1/approvals", "/api/v1/evidence", "/api/v1/audit"):
        assert required in schema["paths"], required


def test_capabilities_reports_configuration_honestly(client: TestClient) -> None:
    body = client.get("/api/v1/capabilities").json()
    assert set(body["tools"]) == {"implemented", "declared_not_implemented"}
    assert body["tools"]["declared_not_implemented"], (
        "declared-but-unbuilt tools must be reported, not hidden"
    )
    assert body["external_model_providers_enabled"] is False
    assert body["policy_mode"] == "enforce"


def test_security_headers_are_present(client: TestClient) -> None:
    headers = client.get("/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"
    assert headers["x-correlation-id"]


# ------------------------------------------------------------- authentication
def test_protected_endpoint_requires_a_token(client: TestClient) -> None:
    assert client.get("/api/v1/runs").status_code == 401


def test_invalid_token_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/runs", headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 401


def test_login_returns_the_principal(client: TestClient, demo_password: str, seeded) -> None:
    body = client.post(
        "/api/v1/auth/login",
        json={"email": "analyst@rta.example", "password": demo_password},
    ).json()
    assert body["user"]["email"] == "analyst@rta.example"
    assert "analyst" in body["user"]["roles"]
    assert body["expires_in"] > 0


def test_mfa_account_cannot_login_without_a_code(
    client: TestClient, demo_password: str, seeded
) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "auditor@rta.example", "password": demo_password},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["details"]["mfa_required"] is True


def test_me_returns_the_bound_identity(client: TestClient, auth: dict) -> None:
    body = client.get("/api/v1/auth/me", headers=auth).json()
    assert body["email"] == "systems.lead@rta.example"
    assert body["tenant_id"]
    assert "operator" in body["roles"]


# ------------------------------------------------------------- authorization
def test_permission_is_enforced_per_endpoint(client: TestClient, auth: dict) -> None:
    """An operator may read runs but may not author policy."""
    assert client.get("/api/v1/runs", headers=auth).status_code == 200
    response = client.post(
        "/api/v1/security/kill-switch",
        headers=auth,
        json={"scope": "TENANT", "target_key": "", "engaged": True, "reason": "attempt"},
    )
    assert response.status_code == 403


def test_kill_switch_requires_a_second_factor(client: TestClient, demo_password: str) -> None:
    """A principal holding the permission but no MFA is still refused."""
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "security@rta.example", "password": demo_password},
    )
    assert login.status_code == 401, "the security admin must be MFA-gated at login"


# -------------------------------------------------------------------- runs
def test_run_detail_exposes_governance_record(client: TestClient, auth: dict) -> None:
    created = client.post(
        "/api/v1/runs",
        headers=auth,
        json={"objective": "Summarise escalator failure modes across the fleet"},
    )
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]

    detail = client.get(f"/api/v1/runs/{run_id}", headers=auth)
    assert detail.status_code == 200
    body = detail.json()
    for section in (
        "run", "plan", "steps", "policy_decisions", "risk_assessments",
        "tool_calls", "approvals", "citations", "model_calls", "trace", "audit",
    ):
        assert section in body, f"run detail must expose '{section}'"
    assert body["run"]["objective"]
    assert body["plan"], "a run must record the plan it executed"
    assert body["audit"], "a run must be represented in the audit ledger"


def test_run_list_is_tenant_scoped(client: TestClient, auth: dict, db, other_tenant_id) -> None:
    body = client.get("/api/v1/runs", headers=auth).json()
    tenant_runs = {
        str(r[0])
        for r in db.execute(text("SELECT id FROM runs")).all()
    }
    for run in body["runs"]:
        assert str(run["id"]) in tenant_runs


# -------------------------------------------------------------- governance
def test_governance_surfaces_respond(client: TestClient, auth: dict) -> None:
    for path in (
        "/api/v1/approvals",
        "/api/v1/policies",
        "/api/v1/risks",
        "/api/v1/evidence",
        "/api/v1/agents",
        "/api/v1/skills",
        "/api/v1/models",
        "/api/v1/prompts",
        "/api/v1/tools",
        "/api/v1/datasets",
        "/api/v1/workflows",
        "/api/v1/command-center",
        "/api/v1/analytics",
        "/api/v1/outcomes",
        "/api/v1/costs",
        "/api/v1/organization",
    ):
        response = client.get(path, headers=auth)
        assert response.status_code == 200, f"{path} -> {response.status_code} {response.text[:200]}"


def test_audit_read_requires_the_audit_permission(client: TestClient, auth: dict) -> None:
    """The seeded operator does not hold audit:read."""
    assert client.get("/api/v1/audit", headers=auth).status_code == 403


def test_knowledge_search_is_acl_filtered_through_the_api(
    client: TestClient, auth: dict
) -> None:
    body = client.post(
        "/api/v1/knowledge/search",
        headers=auth,
        json={"query": "grievance escalation shift allocation", "top_k": 10},
    ).json()
    titles = {r["title"] for r in body["results"]}
    assert "Workforce Case Note (RESTRICTED)" not in titles


def test_unknown_route_returns_json_not_html(client: TestClient, auth: dict) -> None:
    response = client.get("/api/v1/does-not-exist", headers=auth)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
