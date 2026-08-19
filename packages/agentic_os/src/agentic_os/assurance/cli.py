"""Evidence Engine command line.

agentic-evidence collect --environment ci --output artifacts
agentic-evidence report --format markdown
agentic-evidence gate --min-score 70 --require-no-critical-blockers
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentic_os.assurance import evidence
from agentic_os.core.context import system_context
from agentic_os.core.db import bind_tenant, session_scope
from agentic_os.core.registry import REPO_ROOT


def _tenants() -> list[tuple[str, str, str]]:
    """Every tenant, resolved through the provisioning role.

    Cross-tenant enumeration is exactly the operation RLS forbids to the
    application role, so it runs through the one narrow privileged path.
    """
    from sqlalchemy import text

    from agentic_os.core.db import provisioning_session_scope

    with provisioning_session_scope() as session:
        rows = session.execute(
            text(
                "SELECT t.id, t.organization_id, t.slug FROM tenants t "
                "WHERE t.status = 'ACTIVE' AND t.deleted_at IS NULL ORDER BY t.slug"
            )
        ).all()
    return [(str(r[0]), str(r[1]), r[2]) for r in rows]


def _render_markdown(report: evidence.MaturityReport) -> str:
    lines = [
        "# Evidence-Based Maturity Report",
        "",
        f"- **Score**: {report.score}/100",
        f"- **Certified**: {'yes' if report.certified else 'no'}",
        f"- **Environment**: {report.environment}",
        f"- **Commit**: `{report.commit_sha[:12] or 'unknown'}`",
        f"- **Generated**: {report.generated_at}",
        "",
    ]
    if report.test_summary:
        summary = report.test_summary
        lines += [
            f"Test run: {summary.get('tests', 0)} tests, "
            f"{summary.get('failures', 0)} failures, "
            f"{summary.get('errors', 0)} errors, "
            f"{summary.get('skipped', 0)} skipped.",
            "",
        ]
    if report.critical_blockers:
        lines += [
            "## Certification blockers",
            "",
            "The following critical controls are not verified. Certification is refused "
            "regardless of the numerical score.",
            "",
        ]
        for control_id in report.critical_blockers:
            control = next(c for c in report.controls if c.control_id == control_id)
            lines.append(f"- **{control_id}** — {control.title} ({control.status})")
        lines.append("")

    lines += [
        "## Domain scores",
        "",
        "| Domain | Score | Weight | Verified | Failed | Not evidenced |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for domain in sorted(report.domain_scores):
        bucket = report.domain_scores[domain]
        lines.append(
            f"| {domain} | {bucket['score']:.1f} | {bucket['applicable_weight']:.0f} | "
            f"{bucket['passed']} | {bucket['failed']} | {bucket['not_evidenced']} |"
        )

    lines += [
        "",
        "## Controls",
        "",
        "| Control | Domain | Weight | Critical | Status | Test |",
        "|---|---|---:|:---:|---|---|",
    ]
    for control in sorted(report.controls, key=lambda c: c.control_id):
        lines.append(
            f"| {control.control_id} | {control.domain} | {control.weight:g} | "
            f"{'yes' if control.critical else ''} | {control.status} | "
            f"`{control.test_id or '—'}` |"
        )
    return "\n".join(lines) + "\n"


def cmd_collect(args: argparse.Namespace) -> int:
    report, junit_path = evidence.collect(
        environment=args.environment, run_tests=not args.no_run, paths=args.paths or None
    )
    output = Path(args.output)
    bundle = evidence.write_bundle(report, output)
    (output / "MATURITY_REPORT.md").write_text(_render_markdown(report), encoding="utf-8")

    tenants = _tenants()
    for tenant_id, organization_id, _slug in tenants:
        ctx = system_context(tenant_id, organization_id, "evidence-engine")
        with session_scope(ctx) as session:
            bind_tenant(session, tenant_id, actor="evidence-engine")
            evidence.apply_expiry(session, tenant_id)
            evidence.record_evidence(
                session,
                ctx,
                report,
                artifact_uri=str(junit_path),
                artifact_hash=bundle["bundle_hash"],
            )
            if args.certify:
                evidence.record_certification(
                    session,
                    ctx,
                    report,
                    release_tag=args.certify,
                    report_uri=str(output / "MATURITY_REPORT.md"),
                    bundle_hash=bundle["bundle_hash"],
                )

    print(f"score            : {report.score}/100")
    print(f"certified        : {report.certified}")
    print(f"critical blockers: {report.critical_blockers or 'none'}")
    print(f"controls         : {len(report.controls)}")
    print(f"tenants recorded : {len(tenants)}")
    print(f"bundle           : {bundle['bundle_path']} ({bundle['bundle_hash'][:16]}...)")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    tenants = _tenants()
    if not tenants:
        print("no active tenants", file=sys.stderr)
        return 1
    tenant_id, organization_id, _ = tenants[0]
    ctx = system_context(tenant_id, organization_id, "evidence-engine")
    with session_scope(ctx) as session:
        bind_tenant(session, tenant_id, actor="evidence-engine")
        evidence.apply_expiry(session, tenant_id)
        payload = evidence.latest_report(session, tenant_id)
    if payload is None:
        print("no evidence recorded; run 'agentic-evidence collect' first", file=sys.stderr)
        return 1
    if args.format == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        report = evidence.calculate_maturity(
            [
                evidence.ControlEvidence(
                    **{k: v for k, v in c.items() if k in evidence.ControlEvidence.__slots__}
                )
                for c in payload["controls"]
            ],
            environment=payload["environment"],
            commit_sha=payload["commit_sha"],
        )
        print(_render_markdown(report))
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    """Release gate. Non-zero exit fails the pipeline."""
    bundle_path = Path(args.bundle)
    if not bundle_path.exists():
        print(f"no evidence bundle at {bundle_path}", file=sys.stderr)
        return 2
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))

    failures: list[str] = []
    if payload["score"] < args.min_score:
        failures.append(f"score {payload['score']} is below the gate of {args.min_score}")
    if args.require_no_critical_blockers and payload["critical_blockers"]:
        failures.append(f"critical controls not verified: {payload['critical_blockers']}")

    print(f"score            : {payload['score']}/100 (gate {args.min_score})")
    print(f"critical blockers: {payload['critical_blockers'] or 'none'}")
    if failures:
        for failure in failures:
            print(f"GATE FAILED: {failure}", file=sys.stderr)
        return 1
    print("release gate passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentic-evidence", description="Evidence Engine")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="run the suite and derive maturity")
    collect.add_argument("--environment", default="development")
    collect.add_argument("--output", default=str(REPO_ROOT / "artifacts"))
    collect.add_argument("--no-run", action="store_true", help="reuse an existing JUnit report")
    collect.add_argument("--paths", nargs="*", help="restrict the test run to these paths")
    collect.add_argument("--certify", default="", help="record a certification under this tag")
    collect.set_defaults(func=cmd_collect)

    report = sub.add_parser("report", help="render the current maturity picture")
    report.add_argument("--format", choices=["markdown", "json"], default="markdown")
    report.set_defaults(func=cmd_report)

    gate = sub.add_parser("gate", help="fail the build unless the bundle meets the gate")
    gate.add_argument("--bundle", default=str(REPO_ROOT / "artifacts" / "evidence-bundle.json"))
    gate.add_argument("--min-score", type=float, default=70.0)
    gate.add_argument("--require-no-critical-blockers", action="store_true")
    gate.set_defaults(func=cmd_gate)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
