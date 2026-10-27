"""The message catalogue, checked for completeness rather than trusted.

A half-translated interface is the visual equivalent of a skipped test: it
renders, it looks finished, and the English sentence sitting inside an Arabic
page is only obvious to someone who reads Arabic. So every key that exists in
one locale must exist in all of them, and the check runs on every build.
"""

from __future__ import annotations

import re

import pytest
from agentic_os.core.registry import REPO_ROOT

pytestmark = pytest.mark.unit

I18N = REPO_ROOT / "apps" / "web" / "lib" / "i18n.ts"

#: Catalogue bodies, keyed by the constant that holds them.
#: `EN` closes with `} as const;` and `AR` with `};`, so the terminator has to
#: accept both — anchoring on `};` alone swallows EN's block into AR's and the
#: comparison then silently comes down to one catalogue against itself.
_BLOCK = re.compile(
    r"^const (?P<name>EN|AR)\b[^=]*= \{(?P<body>.*?)^\}(?: as const)?;",
    re.DOTALL | re.MULTILINE,
)
_KEY = re.compile(r'^\s*"(?P<key>[^"]+)":', re.MULTILINE)


def _catalogues() -> dict[str, list[str]]:
    source = I18N.read_text(encoding="utf-8")
    found = {m.group("name"): _KEY.findall(m.group("body")) for m in _BLOCK.finditer(source)}
    assert set(found) == {"EN", "AR"}, f"expected EN and AR catalogues, found {sorted(found)}"
    return found


def test_every_english_key_has_an_arabic_translation() -> None:
    catalogues = _catalogues()
    missing = sorted(set(catalogues["EN"]) - set(catalogues["AR"]))
    assert missing == [], f"keys with no Arabic translation: {missing}"


def test_arabic_has_no_keys_the_english_source_lacks() -> None:
    """A stale Arabic key is dead weight that reads as coverage."""
    catalogues = _catalogues()
    extra = sorted(set(catalogues["AR"]) - set(catalogues["EN"]))
    assert extra == [], f"Arabic keys not present in the English source: {extra}"


def test_no_key_is_declared_twice() -> None:
    """A duplicate silently wins and the earlier value never renders."""
    for name, keys in _catalogues().items():
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        assert duplicates == [], f"{name} declares these keys more than once: {duplicates}"


def test_the_arabic_catalogue_is_actually_arabic() -> None:
    """Guards against an untranslated entry copied across to satisfy the count.

    Latin text is allowed where it is the real term — MCP is MCP in both
    languages — so the rule is that each entry must contain Arabic script, not
    that it must contain no Latin.
    """
    source = I18N.read_text(encoding="utf-8")
    block = _BLOCK.search(source[source.index("const AR") :])
    assert block is not None
    arabic = re.compile(r"[؀-ۿ]")
    offenders = [
        key
        for key, value in re.findall(r'^\s*"([^"]+)":\s*(.+?),?$', block.group("body"), re.MULTILINE)
        if not arabic.search(value)
    ]
    assert offenders == [], f"Arabic entries with no Arabic script: {offenders}"


def test_the_untranslated_notice_exists_in_both_locales() -> None:
    """The console must be able to say a surface is not translated.

    Only the application chrome is translated today. The honest presentation of
    that is a notice, in the reader's own language, rather than English text
    silently standing in for Arabic.
    """
    catalogues = _catalogues()
    for name, keys in catalogues.items():
        assert "notice.untranslated" in keys, f"{name} cannot state that a surface is untranslated"
