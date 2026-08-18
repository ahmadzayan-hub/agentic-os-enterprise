"""Evidence Engine.

Maturity is *derived*, never asserted. The engine:

1. runs the test suite and captures a JUnit report,
2. maps each control to the tests named in the control catalogue,
3. records one evidence row per control with the run's environment, commit SHA
   and artifact hash,
4. computes maturity as verified applicable weight over total applicable weight,
5. refuses certification if any critical control is not VERIFIED, whatever the
   score is.

A control with no automated test is reported NOT_EVIDENCED and contributes
zero. There is no path in this module that lets a human set a status.
"""

from __future__ import annotations

import json
import os
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.assurance.audit import AuditEntry, AuditLedger
from agentic_os.core.context import ExecutionContext
from agentic_os.core.crypto import content_hash, sha256_hex
from agentic_os.core.ids import utcnow
from agentic_os.core.registry import CONTROLS_FILE, REPO_ROOT

#: Only these statuses contribute to the maturity numerator.
COUNTING_STATUSES = frozenset({"VERIFIED", "PRODUCTION_PROVEN"})

#: Statuses that block certification when held by a critical control.
BLOCKING_STATUSES = frozenset({"FAILED", "EXPIRED", "NOT_EVIDENCED"})


@dataclass(slots=True)
class TestOutcome:
    node_id: str
    passed: bool
    skipped: bool
    duration_ms: int
    message: str = ""


@dataclass(slots=True)
class ControlEvidence:
    control_id: str
    domain: str
    title: str
    weight: float
    critical: bool
    applicable: bool
    status: str
    test_id: str = ""
    expected: str = ""
    actual: str = ""
    duration_ms: int = 0
    reason: str = ""

    @property
    def counts(self) -> bool:
        return self.status in COUNTING_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_id": self.control_id,
            "domain": self.domain,
            "title": self.title,
            "weight": self.weight,
            "critical": self.critical,
            "applicable": self.applicable,
            "status": self.status,
            "test_id": self.test_id,
            "expected": self.expected,
            "actual": self.actual,
            "duration_ms": self.duration_ms,
            "reason": self.reason,
        }


@dataclass(slots=True)
class MaturityReport:
    score: float
    certified: bool
    controls: list[ControlEvidence] = field(default_factory=list)
    critical_blockers: list[str] = field(default_factory=list)
    domain_scores: dict[str, dict[str, Any]] = field(default_factory=dict)
    environment: str = "development"
    commit_sha: str = ""
    generated_at: str = ""
    test_summary: dict[str, int] = field(default_factory=dict)

    @property
    def applicable_weight(self) -> float:
        return sum(c.weight for c in self.controls if c.applicable)

    @property
    def verified_weight(self) -> float:
        return sum(c.weight for c in self.controls if c.applicable and c.counts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "certified": self.certified,
            "critical_blockers": self.critical_blockers,
            "applicable_weight": self.applicable_weight,
            "verified_weight": self.verified_weight,
            "domain_scores": self.domain_scores,
            "environment": self.environment,
            "commit_sha": self.commit_sha,
            "generated_at": self.generated_at,
            "test_summary": self.test_summary,
            "controls": [c.to_dict() for c in self.controls],
        }


# ---------------------------------------------------------------------------
# Test execution and parsing
# ---------------------------------------------------------------------------
def run_test_suite(
    *, junit_path: Path, paths: list[str] | None = None, extra_args: list[str] | None = None
) -> dict[str, int]:
    """Run pytest and write a JUnit report. Returns the run summary."""
    junit_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        os.environ.get("AGENTIC_PYTEST", ".venv/bin/python"),
        "-m",
        "pytest",
        *(paths or ["tests"]),
        "-q",
        f"--junitxml={junit_path}",
        *(extra_args or []),
    ]
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        command, cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800
    )
    return {
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],  # type: ignore[dict-item]
    }


def parse_junit(junit_path: Path) -> dict[str, TestOutcome]:
    """Parse a JUnit XML report into node-id keyed outcomes."""
    if not junit_path.exists():
        raise FileNotFoundError(f"no JUnit report at {junit_path}")

    outcomes: dict[str, TestOutcome] = {}
    root = ET.parse(junit_path).getroot()
    for case in root.iter("testcase"):
        classname = (case.get("classname") or "").replace(".", "/")
        name = (case.get("name") or "").split("[")[0]
        node_id = f"{classname}.py::{name}"
        failure = case.find("failure") is not None or case.find("error") is not None
        skipped = case.find("skipped") is not None
        message = ""
        for tag in ("failure", "error", "skipped"):
            element = case.find(tag)
            if element is not None:
                message = (element.get("message") or "")[:500]
                break

        existing = outcomes.get(node_id)
        outcome = TestOutcome(
            node_id=node_id,
            passed=not failure and not skipped,
            skipped=skipped,
            duration_ms=int(float(case.get("time", 0)) * 1000),
            message=message,
        )
        # Parameterised cases collapse to one node id: all must pass.
        if existing is None:
            outcomes[node_id] = outcome
        else:
            outcomes[node_id] = TestOutcome(
                node_id=node_id,
                passed=existing.passed and outcome.passed,
                skipped=existing.skipped and outcome.skipped,
                duration_ms=existing.duration_ms + outcome.duration_ms,
                message=existing.message or outcome.message,
            )
    return outcomes


def junit_summary(junit_path: Path) -> dict[str, int]:
    root = ET.parse(junit_path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return {}
    return {
        "tests": int(suite.get("tests", 0)),
        "failures": int(suite.get("failures", 0)),
        "errors": int(suite.get("errors", 0)),
        "skipped": int(suite.get("skipped", 0)),
    }


# ---------------------------------------------------------------------------
# Control evaluation
# ---------------------------------------------------------------------------
def load_controls(path: Path | None = None) -> dict[str, Any]:
    source = path or CONTROLS_FILE
    return yaml.safe_load(source.read_text(encoding="utf-8"))


def evaluate_controls(
    catalogue: dict[str, Any], outcomes: dict[str, TestOutcome]
) -> list[ControlEvidence]:
    """Derive each control's status from the test results. No manual input."""
    evidence: list[ControlEvidence] = []
    for control in catalogue["controls"]:
        test_id = control.get("test", "")
        applicable = bool(control.get("applicable", True))

        if not test_id:
            status, reason, actual, duration = (
                "NOT_EVIDENCED",
                "the control declares no automated test",
                "",
                0,
            )
        elif test_id not in outcomes:
            status, reason, actual, duration = (
                "NOT_EVIDENCED",
                f"named test '{test_id}' did not run",
                "",
                0,
            )
        else:
            outcome = outcomes[test_id]
            duration = outcome.duration_ms
            if outcome.skipped:
                status = "NOT_EVIDENCED"
                reason = "the named test was skipped"
                actual = outcome.message
            elif outcome.passed:
                status = "VERIFIED"
                reason = "the named test passed in this run"
                actual = "test passed"
            else:
                status = "FAILED"
                reason = "the named test failed in this run"
                actual = outcome.message

        evidence.append(
            ControlEvidence(
                control_id=control["id"],
                domain=control["domain"],
                title=control["title"],
                weight=float(control.get("weight", 1)),
                critical=bool(control.get("critical", False)),
                applicable=applicable,
                status=status,
                test_id=test_id,
                expected=control.get("expected", ""),
                actual=actual,
                duration_ms=duration,
                reason=reason,
            )
        )
    return evidence


def calculate_maturity(
    controls: list[ControlEvidence],
    *,
    environment: str = "development",
    commit_sha: str = "",
    test_summary: dict[str, int] | None = None,
) -> MaturityReport:
    """Weighted maturity with a hard critical-control gate.

        score = verified applicable weight / total applicable weight * 100

    A critical control that is not VERIFIED blocks certification outright. This
    is the only place the score is produced, and it reads nothing but control
    statuses.
    """
    applicable = [c for c in controls if c.applicable]
    total = sum(c.weight for c in applicable)
    verified = sum(c.weight for c in applicable if c.counts)
    score = round(verified / total * 100, 2) if total else 0.0

    blockers = sorted(
        c.control_id
        for c in applicable
        if c.critical and (c.status in BLOCKING_STATUSES or not c.counts)
    )

    domain_scores: dict[str, dict[str, Any]] = {}
    for control in applicable:
        bucket = domain_scores.setdefault(
            control.domain,
            {
                "applicable_weight": 0.0,
                "verified_weight": 0.0,
                "passed": 0,
                "failed": 0,
                "not_evidenced": 0,
                "expired": 0,
                "controls": [],
            },
        )
        bucket["applicable_weight"] += control.weight
        if control.counts:
            bucket["verified_weight"] += control.weight
            bucket["passed"] += 1
        elif control.status == "FAILED":
            bucket["failed"] += 1
        elif control.status == "EXPIRED":
            bucket["expired"] += 1
        else:
            bucket["not_evidenced"] += 1
        bucket["controls"].append(control.control_id)

    for bucket in domain_scores.values():
        weight = bucket["applicable_weight"]
        bucket["score"] = round(bucket["verified_weight"] / weight * 100, 2) if weight else 0.0

    return MaturityReport(
        score=score,
        certified=score == 100.0 and not blockers,
        controls=controls,
        critical_blockers=blockers,
        domain_scores=domain_scores,
        environment=environment,
        commit_sha=commit_sha,
        generated_at=utcnow().isoformat(),
        test_summary=test_summary or {},
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def apply_expiry(session: Session, tenant_id: str) -> int:
    """Mark evidence past its control's TTL as EXPIRED. Returns the count."""
    result = session.execute(
        text(
            """
            UPDATE evidence e
               SET status = 'EXPIRED'
              FROM controls c
             WHERE e.tenant_id = :t AND c.tenant_id = :t
               AND c.control_id = e.control_id
               AND e.status IN ('VERIFIED', 'PRODUCTION_PROVEN')
               AND e.collected_at < now() - make_interval(days => c.evidence_ttl_days)
            """
        ),
        {"t": tenant_id},
    )
    return result.rowcount


def record_evidence(
    session: Session,
    ctx: ExecutionContext,
    report: MaturityReport,
    *,
    artifact_uri: str = "",
    artifact_hash: str = "",
) -> int:
    """Persist one evidence row per control and audit the collection."""
    for control in report.controls:
        ttl = session.execute(
            text("SELECT evidence_ttl_days FROM controls WHERE tenant_id = :t AND control_id = :c"),
            {"t": ctx.tenant_id, "c": control.control_id},
        ).scalar()
        session.execute(
            text(
                """
                INSERT INTO evidence (tenant_id, control_id, status, test_id, expected_result,
                                      actual_result, environment, commit_sha, artifact_uri,
                                      artifact_hash, duration_ms, expires_at)
                VALUES (:t, :c, CAST(:s AS evidence_status), :test, :exp, :act, :env, :sha,
                        :uri, :hash, :dur, :expires)
                """
            ),
            {
                "t": ctx.tenant_id,
                "c": control.control_id,
                "s": control.status,
                "test": control.test_id,
                "exp": control.expected[:2000],
                "act": control.actual[:2000],
                "env": report.environment,
                "sha": report.commit_sha,
                "uri": artifact_uri,
                "hash": artifact_hash,
                "dur": control.duration_ms,
                "expires": utcnow() + timedelta(days=int(ttl or 90)),
            },
        )

    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="EVIDENCE",
            action="evidence.collected",
            resource_type="certification",
            resource_id=report.commit_sha or "local",
            payload={
                "score": report.score,
                "certified": report.certified,
                "critical_blockers": report.critical_blockers,
                "controls": len(report.controls),
                "environment": report.environment,
                "artifact_hash": artifact_hash,
            },
        ),
    )
    return len(report.controls)


def record_certification(
    session: Session,
    ctx: ExecutionContext,
    report: MaturityReport,
    *,
    release_tag: str,
    report_uri: str = "",
    bundle_hash: str = "",
) -> str:
    row = session.execute(
        text(
            """
            INSERT INTO certifications (tenant_id, release_tag, commit_sha, environment, score,
                                        certified, critical_blockers, domain_scores, report_uri,
                                        bundle_hash)
            VALUES (:t, :tag, :sha, :env, :score, :certified, CAST(:blockers AS jsonb),
                    CAST(:domains AS jsonb), :uri, :hash)
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id,
            "tag": release_tag,
            "sha": report.commit_sha,
            "env": report.environment,
            "score": report.score,
            "certified": report.certified,
            "blockers": json.dumps(report.critical_blockers),
            "domains": json.dumps(report.domain_scores, default=str),
            "uri": report_uri,
            "hash": bundle_hash,
        },
    ).one()
    return str(row.id)


def latest_report(session: Session, tenant_id: str) -> dict[str, Any] | None:
    """Reconstruct the current maturity picture from stored evidence."""
    rows = session.execute(
        text(
            """
            SELECT DISTINCT ON (c.control_id)
                   c.control_id, c.domain, c.title, c.weight, c.critical, c.applicable,
                   e.status, e.test_id, e.actual_result, e.collected_at, e.expires_at,
                   e.environment, e.commit_sha
            FROM controls c
            LEFT JOIN evidence e
              ON e.control_id = c.control_id AND e.tenant_id = c.tenant_id
            WHERE c.tenant_id = :t
            ORDER BY c.control_id, e.collected_at DESC NULLS LAST
            """
        ),
        {"t": tenant_id},
    ).mappings().all()
    if not rows:
        return None

    now = utcnow()
    controls = []
    for row in rows:
        status = row["status"] or "NOT_EVIDENCED"
        if status in COUNTING_STATUSES and row["expires_at"] and row["expires_at"] < now:
            status = "EXPIRED"
        controls.append(
            ControlEvidence(
                control_id=row["control_id"],
                domain=row["domain"],
                title=row["title"],
                weight=float(row["weight"]),
                critical=bool(row["critical"]),
                applicable=bool(row["applicable"]),
                status=status,
                test_id=row["test_id"] or "",
                actual=row["actual_result"] or "",
            )
        )
    environment = rows[0]["environment"] or "development"
    commit = rows[0]["commit_sha"] or ""
    return calculate_maturity(controls, environment=environment, commit_sha=commit).to_dict()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def current_commit_sha() -> str:
    try:
        return subprocess.run(  # noqa: S603
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:  # pragma: no cover
        return ""


def collect(
    *,
    environment: str = "development",
    junit_path: Path | None = None,
    run_tests: bool = True,
    paths: list[str] | None = None,
) -> tuple[MaturityReport, Path]:
    """Run the suite, evaluate controls and produce a maturity report."""
    junit_path = junit_path or (REPO_ROOT / "artifacts" / "junit.xml")
    if run_tests:
        run_test_suite(junit_path=junit_path, paths=paths)

    outcomes = parse_junit(junit_path)
    catalogue = load_controls()
    controls = evaluate_controls(catalogue, outcomes)
    report = calculate_maturity(
        controls,
        environment=environment,
        commit_sha=current_commit_sha(),
        test_summary=junit_summary(junit_path),
    )
    return report, junit_path


def write_bundle(report: MaturityReport, directory: Path) -> dict[str, str]:
    """Write the machine-readable evidence bundle and return its hashes."""
    directory.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    bundle_path = directory / "evidence-bundle.json"
    bundle_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return {
        "bundle_path": str(bundle_path),
        "bundle_hash": sha256_hex(bundle_path.read_bytes()),
        "content_hash": content_hash(payload),
    }
