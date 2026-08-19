"""Loader and validator for the declarative registries.

Agent contracts, skills, models, tools and policies are versioned YAML assets
in the repository. They are loaded once, validated against each other, and
synchronised into the database at seed/deploy time. Cross-registry validation
runs in CI (``tests/agents/test_contract_validation.py``) so a contract can
never reference a tool, skill or model that does not exist.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[5]

CONTRACTS_DIR = REPO_ROOT / "packages" / "contracts" / "agents"
CONTRACT_SCHEMA = REPO_ROOT / "packages" / "contracts" / "schemas" / "agent-contract.schema.json"
SKILLS_FILE = REPO_ROOT / "skills" / "registry.yaml"
MODELS_FILE = REPO_ROOT / "models" / "registry.yaml"
TOOLS_FILE = REPO_ROOT / "tools" / "registry.yaml"
POLICIES_FILE = REPO_ROOT / "policies" / "registry.yaml"
PROMPTS_DIR = REPO_ROOT / "prompts"
CONTROLS_FILE = REPO_ROOT / "evaluations" / "controls.yaml"


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"registry file not found: {path}")
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@dataclass(frozen=True, slots=True)
class Registries:
    agents: dict[str, dict]
    skills: dict[str, dict]
    models: dict[str, dict]
    tools: dict[str, dict]
    policies: dict[str, dict]
    routing: dict[str, Any]

    def agent(self, key: str) -> dict:
        if key not in self.agents:
            raise KeyError(f"unknown agent '{key}'")
        return self.agents[key]

    def tool(self, key: str) -> dict:
        if key not in self.tools:
            raise KeyError(f"unknown tool '{key}'")
        return self.tools[key]

    def skill(self, key: str) -> dict:
        if key not in self.skills:
            raise KeyError(f"unknown skill '{key}'")
        return self.skills[key]

    def model(self, key: str) -> dict:
        if key not in self.models:
            raise KeyError(f"unknown model '{key}'")
        return self.models[key]


@lru_cache(maxsize=1)
def load_registries() -> Registries:
    agents: dict[str, dict] = {}
    for path in sorted(CONTRACTS_DIR.glob("*.yaml")):
        contract = _load_yaml(path)
        agents[contract["agent"]["id"]] = contract

    skills = {s["key"]: s for s in _load_yaml(SKILLS_FILE)["skills"]}
    models_doc = _load_yaml(MODELS_FILE)
    models = {m["key"]: m for m in models_doc["models"]}
    tools = {t["key"]: t for t in _load_yaml(TOOLS_FILE)["tools"]}
    policies = {p["key"]: p for p in _load_yaml(POLICIES_FILE)["policies"]}

    return Registries(
        agents=agents,
        skills=skills,
        models=models,
        tools=tools,
        policies=policies,
        routing=models_doc.get("routing", {}),
    )


def reset_registry_cache() -> None:
    load_registries.cache_clear()


def validate_registries() -> list[str]:
    """Cross-validate every registry. Returns a list of human-readable problems.

    Enforced invariants:

    * every agent contract satisfies the contract JSON Schema;
    * every *allowed* tool, skill and model an agent names is registered
      (deny-list entries may be forward references to tools not yet built, so
      that a future registration is denied by default);
    * a tool a contract allows is not simultaneously denied;
    * every agent retains at least one model cleared for its data ceiling;
    * the Conductor holds no tool grants at all (Constitution rule 17);
    * every skill's required tools exist;
    * every policy rule declares a valid effect.
    """
    problems: list[str] = []
    registries = load_registries()

    schema = json.loads(CONTRACT_SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    for agent_id, contract in registries.agents.items():
        for error in validator.iter_errors(contract):
            problems.append(f"agent {agent_id}: schema: {'.'.join(map(str, error.path))} {error.message}")

        allowed_tools = set(contract["tools"].get("allowed", []))
        denied_tools = set(contract["tools"].get("denied", []))

        for key in allowed_tools:
            if key not in registries.tools:
                problems.append(f"agent {agent_id}: allows unregistered tool '{key}'")
        overlap = allowed_tools & denied_tools
        if overlap:
            problems.append(f"agent {agent_id}: tool(s) both allowed and denied: {sorted(overlap)}")
        if "*" in denied_tools and allowed_tools:
            problems.append(f"agent {agent_id}: denies all tools but also allows {sorted(allowed_tools)}")

        for key in contract["skills"].get("allowed", []):
            if key not in registries.skills:
                problems.append(f"agent {agent_id}: allows unregistered skill '{key}'")
        for key in contract["models"].get("allowed", []):
            if key not in registries.models:
                problems.append(f"agent {agent_id}: allows unregistered model '{key}'")

        preferred = contract["models"].get("preferred")
        if preferred and preferred not in contract["models"]["allowed"]:
            problems.append(f"agent {agent_id}: preferred model '{preferred}' is not in allowed")

        # An agent must retain at least one model capable of its own data
        # ceiling, otherwise its most sensitive work has no lawful execution
        # path. Per-request classification is enforced separately by the model
        # gateway and the core.data-classification policy, so an agent may also
        # hold lower-clearance models for its less sensitive work.
        max_class = contract["data"]["max_classification"]
        capable = [
            key
            for key in contract["models"].get("allowed", [])
            if key in registries.models
            and _rank(registries.models[key]["max_classification"]) >= _rank(max_class)
        ]
        if not capable:
            problems.append(
                f"agent {agent_id}: may handle {max_class} data but no allowed model "
                f"is cleared above {max_class}"
            )

    # Model registry invariants.
    #
    # The build brief is explicit: no production AI service may use RTA data
    # for external model training unless explicitly approved. A registry entry
    # is where that claim is recorded, so an entry cleared for anything above
    # INTERNAL has to make it, and a RESTRICTED clearance additionally requires
    # that inference stays inside infrastructure the authority operates.
    for model_key, model in registries.models.items():
        clearance = model.get("max_classification", "INTERNAL")
        declared = model.get("allows_training_on_input")
        if _rank(clearance) > _rank("INTERNAL"):
            if declared is None:
                problems.append(
                    f"model {model_key}: cleared for {clearance} but does not declare "
                    "allows_training_on_input"
                )
            elif declared:
                problems.append(
                    f"model {model_key}: cleared for {clearance} while permitting training "
                    "on input; that combination needs explicit approval and its own entry"
                )
        if clearance == "RESTRICTED" and model.get("deployment") not in ("local", "private"):
            problems.append(
                f"model {model_key}: cleared for RESTRICTED but deployment is "
                f"'{model.get('deployment')}'; RESTRICTED inference may not leave "
                "operator-controlled infrastructure"
            )
        if model.get("endpoint") and model.get("provider") != "openai-compatible":
            problems.append(
                f"model {model_key}: names an endpoint but provider "
                f"'{model.get('provider')}' does not take one"
            )

    # Routing rules. A rule whose condition uses an unsupported operator, or
    # which prefers a model that does not exist, is silently dead: it never
    # matches and routing quietly falls through to the next rule. Catching it
    # here is the difference between a policy and a comment.
    _ROUTING_SUFFIXES = ("_in", "_gte", "_lte", "_lt", "_eq")
    for rule in registries.routing.get("rules", []):
        rule_name = rule.get("name", "<unnamed>")
        for condition in rule.get("when") or {}:
            if not condition.endswith(_ROUTING_SUFFIXES):
                problems.append(
                    f"routing rule {rule_name}: condition '{condition}' uses no supported "
                    f"operator suffix {list(_ROUTING_SUFFIXES)}; the rule would never match"
                )
        preferred = rule.get("prefer") or []
        if not preferred:
            problems.append(f"routing rule {rule_name}: prefers no model")
        for key in preferred:
            if key not in registries.models:
                problems.append(f"routing rule {rule_name}: prefers unregistered model '{key}'")

    conductor = registries.agents.get("conductor")
    if conductor is not None and conductor["tools"].get("allowed"):
        problems.append("Architecture Constitution rule 17: the Conductor must hold no tool grants")

    for skill_key, skill in registries.skills.items():
        for tool_key in skill.get("required_tools", []):
            if tool_key not in registries.tools:
                problems.append(f"skill {skill_key}: requires unregistered tool '{tool_key}'")

    valid_effects = {"ALLOW", "DENY", "REQUIRE_APPROVAL", "MONITOR"}
    for policy_key, policy in registries.policies.items():
        for rule in policy.get("rules", []):
            if rule.get("effect") not in valid_effects:
                problems.append(
                    f"policy {policy_key}: rule '{rule.get('name')}' has invalid effect "
                    f"{rule.get('effect')!r}"
                )
            if not rule.get("reason"):
                problems.append(
                    f"policy {policy_key}: rule '{rule.get('name')}' has no reason; denials "
                    "must be explainable"
                )

    return problems


_CLASS_ORDER = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED")


def _rank(classification: str) -> int:
    try:
        return _CLASS_ORDER.index(classification)
    except ValueError:
        return len(_CLASS_ORDER)
