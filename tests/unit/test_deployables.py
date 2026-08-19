"""The service deployables must serve the real platform, not a stand-in.

v3.0 shipped two scaffold services that returned canned answers. Anything that
looks like a working control plane but decides nothing is exactly what the
no-fake-features rule forbids, so these tests assert the stubs stayed gone.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path, name: str):
    """Load a service entry point by path, once.

    Loading the same file twice would create two distinct ``ControlInput``
    classes, and the second module's Pydantic models could not resolve their
    own forward references, so the loaded module is cached.
    """
    import sys

    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_control_plane_service_serves_the_governed_api() -> None:
    module = _load(REPO_ROOT / "services/control-plane/app/main.py", "cp_main")
    paths = set(module.app.openapi()["paths"])
    # The scaffold had exactly one route that decided nothing. The real control
    # plane carries the governed execution path.
    assert "/api/v1/runs" in paths
    assert "/api/v1/approvals" in paths
    assert "/api/v1/policies" in paths
    assert len(paths) > 40


def test_the_evidence_service_uses_the_one_scoring_implementation() -> None:
    module = _load(REPO_ROOT / "services/evidence-engine/app/main.py", "ev_main")
    from agentic_os.assurance.evidence import ControlEvidence, calculate_maturity

    controls = [
        module.ControlInput(control_id="A", domain="d", weight=8, status="VERIFIED"),
        module.ControlInput(control_id="B", domain="d", weight=2, critical=True, status="NOT_EVIDENCED"),
    ]
    served = module.score(module.ScoreRequest(controls=controls, environment="test"))
    reference = calculate_maturity(
        [
            ControlEvidence(
                control_id=c.control_id,
                domain=c.domain,
                title=c.title,
                weight=c.weight,
                critical=c.critical,
                applicable=c.applicable,
                status=c.status,
            )
            for c in controls
        ],
        environment="test",
    )
    assert served["score"] == reference.score == 80.0
    assert served["certified"] is reference.certified is False
    assert served["critical_blockers"] == reference.critical_blockers == ["B"]


def test_a_critical_control_blocks_certification_even_at_a_high_score() -> None:
    module = _load(REPO_ROOT / "services/evidence-engine/app/main.py", "ev_main")
    result = module.score(
        module.ScoreRequest(
            controls=[
                module.ControlInput(control_id="A", domain="d", weight=99, status="VERIFIED"),
                module.ControlInput(control_id="B", domain="d", weight=1, critical=True, status="FAILED"),
            ]
        )
    )
    assert result["score"] == 99.0
    assert result["certified"] is False
    assert result["critical_blockers"] == ["B"]


# ------------------------------------------------------------------ manifests
def test_compose_defines_the_whole_stack() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    assert set(compose["services"]) == {
        "postgres",
        "redis",
        "migrate",
        "api",
        "worker",
        "web",
    }
    # The application must never be handed a superuser connection.
    app_env = compose["services"]["api"]["environment"]
    assert "agentic_app:" in app_env["AGENTIC_DATABASE_URL"]
    assert "postgres:postgres@" not in app_env["AGENTIC_DATABASE_URL"]
    # The database must not be published to the host by default.
    assert "ports" not in compose["services"]["postgres"]


def test_kubernetes_workloads_run_unprivileged_and_carry_no_secrets() -> None:
    base = REPO_ROOT / "infrastructure/kubernetes/base"
    documents = [
        doc for path in sorted(base.glob("*.yaml")) for doc in yaml.safe_load_all(path.read_text()) if doc
    ]
    workloads = [d for d in documents if d["kind"] in ("Deployment", "Job", "CronJob")]
    assert len(workloads) >= 5

    for workload in workloads:
        spec = workload["spec"]
        if workload["kind"] == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        pod = spec["template"]["spec"]
        assert pod["securityContext"]["runAsNonRoot"] is True, workload["metadata"]["name"]
        for container in pod["containers"]:
            security = container["securityContext"]
            assert security["allowPrivilegeEscalation"] is False
            assert security["capabilities"]["drop"] == ["ALL"]
            # Values come from a ConfigMap or a Secret; none are inlined.
            for entry in container.get("env", []):
                assert "valueFrom" in entry or not _looks_secret(entry["name"]), entry


def _looks_secret(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("secret", "password", "token", "key"))


def test_only_the_dr_cronjob_mounts_the_maintenance_identity() -> None:
    """The API and the worker must never hold an identity that can create a database."""
    documents = [
        doc
        for path in sorted((REPO_ROOT / "infrastructure/kubernetes/base").glob("*.yaml"))
        for doc in yaml.safe_load_all(path.read_text())
        if doc
    ]
    holders = []
    for document in documents:
        spec = document.get("spec", {})
        if document["kind"] == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        pod = spec.get("template", {}).get("spec")
        if not pod:
            continue
        for container in pod["containers"]:
            for source in container.get("envFrom", []):
                if source.get("secretRef", {}).get("name") == "agentic-maintenance-secrets":
                    holders.append(document["metadata"]["name"])
    assert holders == ["agentic-dr-exercise"]
