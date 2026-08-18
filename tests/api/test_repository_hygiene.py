"""Repository-level invariants that keep the platform honest."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from agentic_os.core.registry import REPO_ROOT

pytestmark = pytest.mark.unit

SOURCE_ROOT = REPO_ROOT / "packages" / "agentic_os" / "src" / "agentic_os"

REQUIRED_CI_GATES = (
    "ruff",
    "mypy",
    "pytest",
    "tenant-isolation",
    "agent-contract",
    "secret-scan",
    "dependency-scan",
    "sast",
    "sbom",
    "aibom",
    "evidence",
)


def test_ci_pipeline_covers_required_gates() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lowered = workflow.lower()
    missing = [gate for gate in REQUIRED_CI_GATES if gate not in lowered]
    assert missing == [], f"CI pipeline is missing gates: {missing}"


def test_no_hardcoded_secrets_in_source() -> None:
    """Source must never contain a credential-shaped literal."""
    patterns = [
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"(?i)(password|passwd|secret|api_key)\s*=\s*['\"][^'\"]{12,}['\"]"),
    ]
    # Files that legitimately contain example or detector patterns.
    allowlist = {
        "pii.py", "audit.py", "context_firewall.py", "crypto.py", "seed.py", "config.py",
    }
    offenders: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if path.name in allowlist:
            continue
        content = path.read_text(encoding="utf-8")
        for pattern in patterns:
            if pattern.search(content):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {pattern.pattern}")
    assert offenders == [], f"credential-shaped literals found: {offenders}"


def test_no_stray_todo_or_fixme_markers_in_source() -> None:
    """Unresolved markers hide incomplete work; declare it in the registry instead."""
    marker = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in SOURCE_ROOT.rglob("*.py")
        if marker.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"unresolved markers in: {offenders}"


def test_every_registry_file_parses() -> None:
    for relative in (
        "skills/registry.yaml",
        "models/registry.yaml",
        "tools/registry.yaml",
        "policies/registry.yaml",
        "prompts/registry.yaml",
        "evaluations/controls.yaml",
    ):
        path = REPO_ROOT / relative
        assert path.exists(), f"missing {relative}"
        assert yaml.safe_load(path.read_text(encoding="utf-8")), f"{relative} is empty"


def test_control_weights_match_the_declared_domain_model() -> None:
    catalogue = yaml.safe_load((REPO_ROOT / "evaluations" / "controls.yaml").read_text())
    totals: dict[str, float] = {}
    for control in catalogue["controls"]:
        totals[control["domain"]] = totals.get(control["domain"], 0) + control["weight"]
    for domain, declared in catalogue["domains"].items():
        assert totals.get(domain, 0) == declared, (
            f"domain '{domain}' declares weight {declared} but its controls total "
            f"{totals.get(domain, 0)}"
        )
    assert sum(catalogue["domains"].values()) == 100


def test_every_control_test_reference_points_at_a_real_file() -> None:
    catalogue = yaml.safe_load((REPO_ROOT / "evaluations" / "controls.yaml").read_text())
    missing = []
    for control in catalogue["controls"]:
        test_id = control.get("test")
        if not test_id:
            continue
        file_part = test_id.split("::")[0]
        if not (REPO_ROOT / file_part).exists():
            missing.append(f"{control['id']} -> {file_part}")
    assert missing == [], f"controls referencing missing test files: {missing}"


def test_env_example_documents_every_setting_without_values() -> None:
    content = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "AGENTIC_JWT_SECRET=" in content
    # Only keys that hold a credential must be blank; settings that merely
    # *name* a backend (AGENTIC_SECRET_BACKEND=env) legitimately carry a value.
    credential_key = re.compile(r"^AGENTIC_.*(_SECRET|_KEY|_TOKEN|_PASSWORD)$")
    for line in content.splitlines():
        if not line.startswith("AGENTIC_") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if credential_key.match(name):
            assert value.strip() == "", f"credential setting must ship blank: {line}"


def test_gitignore_excludes_local_state() -> None:
    content = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (".env", ".venv/", "__pycache__/", ".data/"):
        assert entry in content, f".gitignore must exclude {entry}"
