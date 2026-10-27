#!/usr/bin/env python3
"""Generate an AI Bill of Materials.

An SBOM lists software dependencies. An AIBOM lists the things that determine
what an AI system *does*: models, prompts, agent contracts, skills, embedding
models, tools, MCP servers, policies and guardrails — each with the version and
content hash that was in effect for the release.

Emitted in CycloneDX 1.5 JSON so it sits alongside the SBOM in the same tooling.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/agentic_os/src"))

from agentic_os.core.crypto import content_hash  # noqa: E402
from agentic_os.core.registry import PROMPTS_DIR, load_registries  # noqa: E402


def _commit() -> str:
    try:
        return subprocess.run(  # noqa: S603, S607 - git resolved from PATH by design
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - resolved from PATH by design
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _component(ctype: str, name: str, version: str, digest: str, properties: dict) -> dict:
    return {
        "type": ctype,
        "bom-ref": f"{ctype}:{name}@{version}",
        "name": name,
        "version": version,
        "hashes": [{"alg": "SHA-256", "content": digest}],
        "properties": [{"name": k, "value": str(v)} for k, v in properties.items()],
    }


def build() -> dict:
    registries = load_registries()
    components: list[dict] = []

    for key, model in registries.models.items():
        components.append(
            _component(
                "machine-learning-model",
                key,
                str(model.get("provider_model_id", "unknown")),
                content_hash(model),
                {
                    "provider": model["provider"],
                    "deployment": model["deployment"],
                    "approval_state": model.get("approval_state", "PENDING"),
                    "max_classification": model["max_classification"],
                    "residency": model.get("residency", "global"),
                    "context_window": model["context_window"],
                    "known_limitations": (model.get("known_limitations") or "").strip(),
                },
            )
        )

    import yaml

    prompt_registry = yaml.safe_load((PROMPTS_DIR / "registry.yaml").read_text(encoding="utf-8"))
    for prompt in prompt_registry.get("prompts", []):
        body = (PROMPTS_DIR / prompt["body_file"]).read_text(encoding="utf-8")
        components.append(
            _component(
                "data",
                f"prompt/{prompt['key']}",
                prompt["version"],
                content_hash(body),
                {
                    "owning_agent": prompt.get("owning_agent", ""),
                    "deployment_status": prompt.get("deployment_status", "DRAFT"),
                    "purpose": prompt["purpose"],
                    "evaluation_suites": ",".join(prompt.get("evaluation_suites", [])),
                },
            )
        )

    for agent_id, contract in registries.agents.items():
        components.append(
            _component(
                "application",
                f"agent/{agent_id}",
                contract["agent"]["version"],
                content_hash(contract),
                {
                    "owner": contract["agent"]["owner"],
                    "risk_class": contract["agent"]["risk_class"],
                    "max_autonomy": contract["autonomy"]["max_level"],
                    "max_classification": contract["data"]["max_classification"],
                    "allowed_models": ",".join(contract["models"]["allowed"]),
                    "allowed_tools": ",".join(contract["tools"].get("allowed", [])),
                },
            )
        )

    for key, skill in registries.skills.items():
        components.append(
            _component(
                "library",
                f"skill/{key}",
                "1.0.0",
                content_hash(skill),
                {
                    "execution_mode": skill["execution_mode"],
                    "risk_class": skill.get("risk_class", "LOW"),
                    "evaluation_threshold": skill.get("evaluation_threshold", 0),
                },
            )
        )

    for key, tool in registries.tools.items():
        components.append(
            _component(
                "application",
                f"tool/{key}",
                "1.0.0",
                content_hash(tool),
                {
                    "kind": tool["kind"],
                    "side_effect": tool["side_effect"],
                    "implementation_status": tool["implementation_status"],
                    "requires_approval": tool.get("requires_approval", False),
                    "connector": tool.get("connector_key", ""),
                },
            )
        )

    for key, policy in registries.policies.items():
        components.append(
            _component(
                "data",
                f"policy/{key}",
                "1.0.0",
                content_hash(policy["rules"]),
                {
                    "category": policy.get("category", ""),
                    "enforcement": policy.get("enforcement", "ENFORCE"),
                    "rule_count": len(policy["rules"]),
                },
            )
        )

    from agentic_os.core.config import Settings

    settings = Settings()
    components.append(
        _component(
            "machine-learning-model",
            "embedding/" + settings.embedding_provider,
            "1.0.0",
            content_hash(
                {"provider": settings.embedding_provider, "dimensions": settings.embedding_dimensions}
            ),
            {
                "dimensions": settings.embedding_dimensions,
                "role": "retrieval",
                "generative": False,
            },
        )
    )

    components.append(
        _component(
            "application",
            "guardrail/context-firewall",
            "1.0.0",
            content_hash({"component": "context_firewall"}),
            {
                "role": "prompt-injection defence",
                "trust_tiers": 8,
                "instruction_bearing_tiers": 2,
            },
        )
    )

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": {
                "type": "application",
                "name": "agentic-os-enterprise",
                "version": "3.1.0",
            },
            "properties": [
                {"name": "commit", "value": _commit()},
                {
                    "name": "aibom.note",
                    "value": (
                        "Lists every artefact that determines system behaviour: models, prompts, "
                        "agent contracts, skills, tools, policies, embeddings and guardrails."
                    ),
                },
            ],
        },
        "components": components,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an AI Bill of Materials")
    parser.add_argument("--output", default="artifacts/aibom.json")
    args = parser.parse_args()

    document = build()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")

    counts: dict[str, int] = {}
    for component in document["components"]:
        prefix = component["name"].split("/")[0] if "/" in component["name"] else component["type"]
        counts[prefix] = counts.get(prefix, 0) + 1
    print(f"AIBOM written to {path} with {len(document['components'])} components")
    for key in sorted(counts):
        print(f"  {key:<12} {counts[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
