"""Repository-level invariants that keep the platform honest."""

from __future__ import annotations

import re

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


def test_the_test_job_cannot_pass_by_skipping_its_services() -> None:
    """Every service CI provisions must also be required.

    Otherwise a service that dies mid-job skips every test depending on it and
    the job still reports green, because a skip and a pass are indistinguishable
    in an exit code.
    """
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = workflow["jobs"]["tests"]
    declared = job.get("env", {}).get("AGENTIC_REQUIRE_SERVICES", "")
    required = {part.strip() for part in str(declared).split(",") if part.strip()}

    # The service container name in CI, mapped to the gate name in conftest.
    gate_for_service = {"postgres": "db", "redis": "redis"}
    provisioned = {gate_for_service[name] for name in job.get("services", {}) if name in gate_for_service}

    assert provisioned <= required, (
        f"CI provisions {sorted(provisioned)} but only requires {sorted(required)}; "
        f"tests needing {sorted(provisioned - required)} would skip silently"
    )


def test_a_piped_ci_step_cannot_swallow_its_own_failure() -> None:
    """`cmd | tee` under `bash -e` exits with tee's status, which is always 0.

    A gate written that way can never fail the job. Any step that pipes must
    set pipefail in the same block.
    """
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    offenders = []
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            script = step.get("run", "")
            if "|" not in script or "pipefail" in script:
                continue
            for line in script.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "|" not in stripped:
                    continue
                # `||`, `|>` and YAML block markers are not pipelines.
                if "||" in stripped or stripped.endswith("|"):
                    continue
                offenders.append(f"{job_name}: {step.get('name', stripped)}")
                break
    assert offenders == [], f"CI steps pipe without pipefail, so a failure would report success: {offenders}"


def test_the_type_check_is_not_advisory() -> None:
    """mypy must fail the build, not report its findings in green.

    It ran with `continue-on-error: true` while a 42-error backlog stood, which
    meant the pipeline printed those errors and passed anyway — the same shape
    as a skipped test or a check whose exit status came from `tee`. Two of those
    42 turned out to describe a real inversion in the clearance arithmetic. The
    backlog is cleared; this stops the escape hatch being reinstated the next
    time an error is inconvenient.
    """
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "mypy" in str(step.get("run", ""))
    ]
    assert steps, "no CI step runs mypy"
    tolerated = [s.get("name", s["run"]) for s in steps if s.get("continue-on-error")]
    assert tolerated == [], f"mypy steps allowed to fail silently: {tolerated}"


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
        "pii.py",
        "audit.py",
        "context_firewall.py",
        "crypto.py",
        "seed.py",
        "config.py",
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
            f"domain '{domain}' declares weight {declared} but its controls total {totals.get(domain, 0)}"
        )
    # The total is declared, not fixed at 100. A catalogue that had to sum to
    # 100 could only admit a new unmet control by shrinking an existing one,
    # which would raise the score for doing nothing.
    assert sum(catalogue["domains"].values()) == catalogue["total_weight"]


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


def _routes():
    """Every concrete route, with its mounted path and declared permission."""
    from agentic_os.api.app import API_PREFIX, create_app

    app = create_app()
    app.openapi()

    def walk(routes, mounted: bool = False):
        """Yield concrete routes, noting which came from an included router.

        Only those carry their path without the mount prefix.
        """
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                yield from walk(included.routes, mounted=True)
            else:
                yield route, mounted

    #: FastAPI's own documentation endpoints, which the application does not
    #: define and which serve no tenant data.
    generated = {f"{API_PREFIX}/docs", f"{API_PREFIX}/openapi.json", "/docs/oauth2-redirect"}

    for route, mounted in walk(app.routes):
        path = getattr(route, "path", "")
        if not path or path in generated:
            continue
        full = API_PREFIX + path if mounted else path
        permission = ""
        for dependency in getattr(route, "dependencies", []) or []:
            call = getattr(dependency, "dependency", None)
            permission = getattr(call, "required_permission", "") or permission
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            yield method, full, permission


#: Endpoints that legitimately declare no permission: authentication itself,
#: the static capability manifest, and the probes.
_UNAUTHENTICATED = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/auth/me"),
    ("GET", "/api/v1/capabilities"),
    ("GET", "/health"),
    ("GET", "/ready"),
}


def test_every_route_declares_a_permission_or_is_explicitly_exempt() -> None:
    """A route with no declared permission is only safe if it is meant to be.

    Enumerating the exemptions here means adding an unauthorised endpoint is a
    deliberate act that shows up in a diff, rather than an omission.
    """
    undeclared = {
        (method, path)
        for method, path, permission in _routes()
        if not permission and path.startswith("/api/v1")
    }
    unexpected = undeclared - {p for p in _UNAUTHENTICATED if p[1].startswith("/api/v1")}
    assert unexpected == set(), f"routes with no permission requirement: {sorted(unexpected)}"


def test_the_generated_api_reference_matches_the_application() -> None:
    """The reference is generated; a stale one is a documentation defect."""
    reference = (REPO_ROOT / "docs" / "api" / "API_REFERENCE.md").read_text(encoding="utf-8")
    for method, path, permission in _routes():
        if not path.startswith("/api/v1"):
            continue
        assert f"| {method} | `{path}` |" in reference, (
            f"{method} {path} is missing from docs/api/API_REFERENCE.md; "
            "regenerate it with scripts/generate_api_reference.py"
        )
        if permission:
            assert f"| {method} | `{path}` | `{permission}` |" in reference, (
                f"{method} {path} requires {permission} but the reference disagrees; "
                "regenerate it with scripts/generate_api_reference.py"
            )


# ------------------------------------------------------------- README facts
#
# Every headline count in the README was written by hand once and then drifted:
# 60 controls when there were 70, 23 accessibility surfaces when there were 25,
# 332 tests when there were 449. None of it was dishonest when written and all
# of it was wrong by the time anyone read it. The block is generated now, and
# this fails when it stops matching — the same treatment the API reference gets.


def test_the_readme_headline_numbers_match_the_repository() -> None:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import repo_facts

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert repo_facts.START in readme and repo_facts.END in readme, (
        "the README lost its generated region; restore the markers"
    )

    facts = repo_facts.collect()
    # The two evidence-derived fields are excluded from the comparison: they
    # come from artefacts a fresh checkout has not produced, and a test that
    # demanded them would fail for want of a test run rather than for drift.
    facts["executed_tests"] = None
    facts["accessibility"] = None
    expected = repo_facts.render(facts)

    start = readme.index(repo_facts.START)
    end = readme.index(repo_facts.END) + len(repo_facts.END)
    actual = readme[start:end]

    def structural(block: str) -> list[str]:
        skip = ("| Tests |", "| Accessibility |")
        return [line for line in block.splitlines() if not line.startswith(skip)]

    assert structural(actual) == structural(expected), (
        "the README's generated block is out of date; regenerate it with `python scripts/repo_facts.py`"
    )


def test_the_generator_reports_missing_evidence_rather_than_inventing_it() -> None:
    """A count nobody executed is not evidence, and must not read like one."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import repo_facts

    blank = repo_facts.collect()
    blank["executed_tests"] = None
    blank["accessibility"] = None
    rendered = repo_facts.render(blank)
    assert "not measured in this checkout" in rendered
    assert "not audited in this checkout" in rendered
    # And it must not fall back to a plausible-looking zero.
    assert "| Tests | 0" not in rendered
    assert "0 scans" not in rendered
