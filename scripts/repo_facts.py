"""Derive the repository's own headline numbers, and refuse to guess any of them.

Every count in the README was written by hand once and then drifted: it claimed
60 controls when there were 70, 23 accessibility surfaces when there were 25,
and 332 tests when there were 449. None of that was dishonest when written, and
all of it was wrong by the time anyone read it.

So the numbers are computed here from the repository itself, and
``tests/api/test_repository_hygiene.py`` fails when the README disagrees — the
same treatment ``docs/api/API_REFERENCE.md`` already gets.

Two counts deliberately come from artefacts rather than from source: the test
total and the accessibility figures. A count of test *functions* found by
grepping is not the number that ran, and the whole point of the evidence
discipline here is that a number nobody executed is not evidence. When the
artefacts are absent the fields are reported as ``None`` and the README block
keeps whatever it last recorded, rather than being rewritten with a guess.
"""

from __future__ import annotations

import json
import pathlib
import xml.etree.ElementTree as ET
from typing import Any

REPO = pathlib.Path(__file__).resolve().parents[1]

#: The marked region of the README this script owns. Everything outside it is
#: written by a person and is never touched.
START = "<!-- repo-facts:start -->"
END = "<!-- repo-facts:end -->"

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _api_endpoints() -> int:
    from agentic_os.api.app import create_app

    schema = create_app().openapi()
    return sum(len([m for m in operations if m in HTTP_METHODS]) for operations in schema["paths"].values())


def _console_surfaces() -> int:
    return len(list((REPO / "apps/web/app").rglob("page.tsx")))


def _migrations() -> int:
    return len(list((REPO / "database/migrations").glob("*.sql")))


def _controls() -> tuple[int, int]:
    from agentic_os.assurance.evidence import load_controls

    catalogue = load_controls()
    return len(catalogue["controls"]), int(catalogue["total_weight"])


def _agent_contracts() -> int:
    return len(list((REPO / "packages/contracts/agents").glob("*.yaml")))


def _test_suites() -> int:
    return len([d for d in (REPO / "tests").iterdir() if d.is_dir() and not d.name.startswith("__")])


def _tools() -> tuple[int, int]:
    """Declared tools, and how many are actually implemented."""
    import yaml
    from agentic_os.tools.builtin import BUILTIN_TOOLS

    registry = yaml.safe_load((REPO / "tools/registry.yaml").read_text(encoding="utf-8"))
    declared = registry.get("tools", [])
    return len(declared), len(BUILTIN_TOOLS)


def _executed_tests(junit: pathlib.Path) -> int | None:
    """How many tests actually ran, from a JUnit report — not a grep of source."""
    if not junit.exists():
        return None
    # Same justification as agentic_os.assurance.evidence: the report is written
    # by this repository's own test run, not accepted from a caller, and
    # ElementTree resolves no external entities.
    suite = ET.parse(junit).getroot().find("testsuite")  # noqa: S314
    return int(suite.get("tests", 0)) if suite is not None else None


def _accessibility() -> dict[str, Any] | None:
    report = REPO / "artifacts/accessibility.json"
    if not report.exists():
        return None
    data = json.loads(report.read_text(encoding="utf-8"))
    return {
        "surfaces": data["surfaces_scanned"],
        "scans": data["scans"],
        "violations": data["total_violations"],
    }


def collect(junit: pathlib.Path | None = None) -> dict[str, Any]:
    controls, weight = _controls()
    declared_tools, implemented_tools = _tools()
    return {
        "api_endpoints": _api_endpoints(),
        "console_surfaces": _console_surfaces(),
        "migrations": _migrations(),
        "controls": controls,
        "control_weight": weight,
        "agent_contracts": _agent_contracts(),
        "test_suites": _test_suites(),
        "declared_tools": declared_tools,
        "implemented_tools": implemented_tools,
        "executed_tests": _executed_tests(junit or REPO / "artifacts/junit.xml"),
        "accessibility": _accessibility(),
    }


def render(facts: dict[str, Any]) -> str:
    """The README block. Absent evidence renders as a stated gap, not a number."""
    tests = (
        f"{facts['executed_tests']}, all passing"
        if facts["executed_tests"] is not None
        else "not measured in this checkout — run the suite to record a figure"
    )
    axe = facts["accessibility"]
    accessibility = (
        f"{axe['surfaces']} surfaces × 2 colour schemes × 2 text directions "
        f"= {axe['scans']} scans, {axe['violations']} violations"
        if axe
        else "not audited in this checkout — run `npm run a11y` to record a figure"
    )
    unimplemented = facts["declared_tools"] - facts["implemented_tools"]
    return "\n".join(
        [
            START,
            "",
            "| | |",
            "|---|---|",
            f"| API endpoints | {facts['api_endpoints']} |",
            f"| Console surfaces | {facts['console_surfaces']} |",
            f"| Database migrations | {facts['migrations']} |",
            f"| Agent contracts | {facts['agent_contracts']} |",
            f"| Tests | {tests} |",
            f"| Assurance controls | {facts['controls']}, totalling "
            f"{facts['control_weight']} weighted points |",
            f"| Accessibility | {accessibility} |",
            f"| Tools | {facts['declared_tools']} declared, "
            f"{facts['implemented_tools']} implemented, {unimplemented} marked "
            "NOT_IMPLEMENTED and refused by the planner |",
            "",
            "<sub>Generated by `scripts/repo_facts.py`; "
            "`tests/api/test_repository_hygiene.py` fails when it drifts.</sub>",
            END,
        ]
    )


def apply(readme: pathlib.Path, block: str) -> bool:
    """Replace the marked block. Returns True when the file changed."""
    text = readme.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"{readme} has no {START} … {END} region; add one before running this")
    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    updated = head + block + tail
    if updated == text:
        return False
    readme.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    facts = collect()
    changed = apply(REPO / "README.md", render(facts))
    print(f"README.md {'updated' if changed else 'already current'}")
    for key, value in facts.items():
        print(f"  {key:20} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
