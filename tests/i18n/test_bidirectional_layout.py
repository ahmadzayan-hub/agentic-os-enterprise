"""Right-to-left support, enforced rather than asserted in a document.

The platform is for an authority that works in Arabic. Direction is therefore
a property of the document, set from the active locale, and the stylesheet must
never contradict it. These tests are the reason that stays true: a physical
`margin-left` added six months from now flips the wrong way in Arabic and looks
correct to whoever wrote it in English.
"""

from __future__ import annotations

import re

import pytest
from agentic_os.core.registry import REPO_ROOT

pytestmark = pytest.mark.unit

WEB = REPO_ROOT / "apps" / "web"
STYLESHEET = WEB / "app" / "globals.css"
I18N = WEB / "lib" / "i18n.ts"

#: Properties that pin a rule to a physical side of the screen. Each has a
#: logical equivalent that follows the writing direction instead.
PHYSICAL = {
    "margin-left": "margin-inline-start",
    "margin-right": "margin-inline-end",
    "padding-left": "padding-inline-start",
    "padding-right": "padding-inline-end",
    "border-left": "border-inline-start",
    "border-right": "border-inline-end",
    "float": "no float; use flex or grid",
}


def _without_comments(css: str) -> str:
    """Blank out comments, preserving newlines so line numbers stay truthful."""
    return re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group()), css, flags=re.DOTALL)


def _find(css: str, pattern: str) -> list[str]:
    """Every match of a declaration pattern, reported with its line number.

    Deliberately not line-anchored. `.probe { margin-left: 4px; }` is one line
    with the property in the middle of it, and a guard that only looked at the
    start of a line would wave it through — which is how a check that cannot
    fail ends up reading exactly like a check that passed.
    """
    text = _without_comments(css)
    return [
        f"globals.css:{text.count(chr(10), 0, m.start()) + 1} `{m.group().strip()}`"
        for m in re.finditer(pattern, text)
    ]


def test_no_stylesheet_rule_pins_itself_to_a_physical_side() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    offenders: list[str] = []
    for prop, replacement in PHYSICAL.items():
        for hit in _find(css, rf"(?<![\w-]){re.escape(prop)}\s*:[^;{{}}]*"):
            offenders.append(f"{hit} — use {replacement}")
    assert offenders == [], "physical direction properties break right-to-left:\n" + "\n".join(offenders)


def test_no_rule_aligns_text_to_a_physical_side() -> None:
    """`text-align: left` is `start` in English and wrong in Arabic."""
    css = STYLESHEET.read_text(encoding="utf-8")
    offenders = _find(css, r"(?<![\w-])text-align\s*:\s*(?:left|right)\b")
    assert offenders == [], "use text-align: start / end:\n" + "\n".join(offenders)


def test_no_rule_offsets_from_a_physical_edge() -> None:
    """`left:`/`right:` offsets must be `inset-inline-start`/`-end`.

    The lookbehind keeps `border-left:` out of this test's way — that one is
    reported by the physical-property test above, with its own advice.
    """
    css = STYLESHEET.read_text(encoding="utf-8")
    offenders = _find(css, r"(?<![\w-])(?:left|right)\s*:[^;{}]*")
    assert offenders == [], "use inset-inline-start / inset-inline-end:\n" + "\n".join(offenders)


def test_the_document_sets_direction_from_the_locale() -> None:
    """`<html dir>` must be computed, never hard-coded."""
    layout = (WEB / "app" / "layout.tsx").read_text(encoding="utf-8")
    assert 'lang="en"' not in layout, "the document language is hard-coded to English"
    assert "dir={dir}" in layout, "the document does not set a direction"
    assert layout.count("<html lang={locale} dir={dir}>") == 2, (
        "both the authenticated and unauthenticated shells must set lang and dir"
    )
