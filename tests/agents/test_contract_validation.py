"""Agent contracts and the declarative registries must stay mutually consistent."""

from __future__ import annotations

import pytest

from agentic_os.core.registry import load_registries, validate_registries
from agentic_os.identity.permissions import (
    CATALOGUE_BY_ID,
    SYSTEM_ROLES,
    validate_catalogue,
)

pytestmark = pytest.mark.unit

DOMAIN_AGENTS = {
    "conductor",
    "sales",
    "finance",
    "customer",
    "operations",
    "marketing",
    "analytics",
    "knowledge",
    "engineering",
    "communications",
}


def test_registries_are_internally_consistent() -> None:
    problems = validate_registries()
    assert problems == [], "\n".join(problems)


def test_permission_catalogue_is_consistent() -> None:
    assert validate_catalogue() == []


def test_all_declared_domain_agents_exist() -> None:
    registries = load_registries()
    missing = DOMAIN_AGENTS - set(registries.agents)
    assert missing == set(), f"missing agent contracts: {sorted(missing)}"


def test_conductor_holds_no_tool_authority() -> None:
    """Architecture Constitution rule 17."""
    conductor = load_registries().agent("conductor")
    assert conductor["tools"]["allowed"] == []
    assert conductor["limits"]["max_tool_calls"] == 0


def test_no_agent_may_self_authorise_a4() -> None:
    """No contract may grant A4 as an autonomous ceiling."""
    for agent_id, contract in load_registries().agents.items():
        assert contract["autonomy"]["max_level"] != "A4", (
            f"agent {agent_id} claims autonomous A4; consequential actions must "
            "route through the approval engine"
        )


def test_agents_handling_money_have_zero_execution_limit() -> None:
    finance = load_registries().agent("finance")
    assert finance["limits"]["financial_execution_limit_usd"] == 0


def test_every_agent_declares_budgets_and_slos() -> None:
    for agent_id, contract in load_registries().agents.items():
        limits = contract["limits"]
        assert limits["token_budget"] > 0, agent_id
        assert limits["max_runtime_seconds"] > 0, agent_id
        assert limits["cost_budget_usd"] >= 0, agent_id
        assert contract["requirements"]["evaluation"]["min_score"] > 0, agent_id


def test_every_agent_requires_citations_and_provenance() -> None:
    for agent_id, contract in load_registries().agents.items():
        assert contract["requirements"]["citations"] is True, agent_id
        assert contract["requirements"]["provenance"] is True, agent_id


def test_prohibited_domains_cover_sensitive_personal_data() -> None:
    """No agent may be silently permitted employee medical or payroll data."""
    for agent_id, contract in load_registries().agents.items():
        prohibited = set(contract["data"]["prohibited_domains"])
        permitted = set(contract["data"]["permitted_domains"])
        assert not ({"employee_medical", "payroll"} & permitted), agent_id
        assert prohibited, f"{agent_id} declares no prohibited domains"


def test_write_and_external_tools_require_elevated_autonomy() -> None:
    for key, tool in load_registries().tools.items():
        if tool["side_effect"] in ("EXTERNAL", "FINANCIAL", "DELETE"):
            assert tool["min_autonomy"] == "A4", f"{key} must require A4"
            assert tool.get("requires_approval") is True, f"{key} must require approval"
        if tool["side_effect"] == "READ":
            assert tool.get("requires_approval", False) is False, key


def test_irreversible_tools_declare_verification() -> None:
    for key, tool in load_registries().tools.items():
        if tool.get("reversibility") in ("IRREVERSIBLE", "PARTIAL"):
            assert tool.get("verification_mode", "NONE") != "NONE", (
                f"{key} is not fully reversible and must declare a verification mode"
            )


def test_system_roles_only_reference_catalogued_permissions() -> None:
    for role in SYSTEM_ROLES:
        for pid in role.permissions:
            assert pid in CATALOGUE_BY_ID, f"{role.slug} -> {pid}"


def test_privileged_roles_require_mfa() -> None:
    privileged = {"platform_admin", "security_admin", "governance_admin", "auditor", "approver"}
    for role in SYSTEM_ROLES:
        if role.slug in privileged:
            assert role.requires_mfa, f"{role.slug} must require MFA"


def test_read_only_roles_cannot_write() -> None:
    auditor = next(r for r in SYSTEM_ROLES if r.slug == "auditor")
    writes = [p for p in auditor.permissions if not p.endswith((":read", ":verify"))]
    assert writes == [], f"auditor holds write permissions: {writes}"
    assert auditor.max_autonomy == "A0"


def test_deterministic_skills_declare_full_thresholds() -> None:
    """A deterministic skill is reproducible, so its bar is exactness."""
    for key, skill in load_registries().skills.items():
        if skill["execution_mode"] == "DETERMINISTIC" and key not in {"search", "classify"}:
            assert skill["evaluation_threshold"] >= 0.9, key


def test_not_implemented_tools_are_explicit() -> None:
    """A tool is either implemented or clearly marked; there is no third state."""
    for key, tool in load_registries().tools.items():
        assert tool["implementation_status"] in ("IMPLEMENTED", "NOT_IMPLEMENTED"), key
