"""Properties of the source tree itself that a reader has to be able to trust."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Bidirectional and invisible formatting characters. Source containing these
#: can render to a reviewer differently from how the interpreter executes it,
#: which is the Trojan Source class of attack (CVE-2021-42574).
BIDI_AND_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")


def _source_files() -> list[Path]:
    roots = [
        REPO_ROOT / "packages" / "agentic_os" / "src",
        REPO_ROOT / "tests",
        REPO_ROOT / "scripts",
        REPO_ROOT / "apps" / "web" / "app",
        REPO_ROOT / "apps" / "web" / "components",
        REPO_ROOT / "apps" / "web" / "lib",
    ]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for suffix in ("*.py", "*.ts", "*.tsx", "*.mjs"):
            files.extend(p for p in root.rglob(suffix) if "__pycache__" not in p.parts)
    return files


def test_no_source_file_contains_bidirectional_control_characters() -> None:
    """The one place these characters belong is a pattern that *detects* them.

    The context firewall's rules are written as ``\\uXXXX`` escapes for exactly
    this reason: the regex still matches the same 27 codepoints, but the source
    stays plain ASCII and reads the way it executes.
    """
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in BIDI_AND_INVISIBLE.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line} contains U+{ord(match.group()):04X}")
    assert offenders == [], "bidirectional or invisible characters in source: " + "; ".join(offenders)


def test_the_firewall_still_detects_the_characters_it_no_longer_embeds() -> None:
    """Escaping the pattern must not have weakened what it catches."""
    from agentic_os.ai.context_firewall import detect

    for codepoint in (0x200B, 0x200F, 0x202A, 0x202E, 0x2060, 0x206F, 0xFEFF):
        findings = detect(f"approve the refund{chr(codepoint)} immediately")
        assert any(f.pattern == "invisible_characters" for f in findings), (
            f"U+{codepoint:04X} is no longer detected"
        )


def test_secret_scan_exceptions_only_ever_cover_test_fixtures() -> None:
    """A suppression file is a place real leaks go to hide. Bound what it can cover.

    Every gitleaks exception must be a full finding fingerprint — which pins it
    to one commit, one file and one line — and must point at a file under
    ``tests/``. An exception for application code, or a bare path or pattern
    that could match more than the finding it was written for, fails here.
    """
    ignore_file = REPO_ROOT / ".gitleaksignore"
    if not ignore_file.exists():
        return

    entries = [
        line.strip()
        for line in ignore_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for entry in entries:
        parts = entry.split(":")
        assert len(parts) == 4, f"not a finding fingerprint (expected commit:path:rule:line): {entry}"
        commit, path, _rule, line_no = parts
        assert len(commit) == 40 and all(c in "0123456789abcdef" for c in commit), (
            f"first field must be a full commit sha: {entry}"
        )
        assert line_no.isdigit(), f"last field must be a line number: {entry}"
        assert path.startswith("tests/"), (
            f"secret-scan exceptions are only permitted for test fixtures, not {path}"
        )
