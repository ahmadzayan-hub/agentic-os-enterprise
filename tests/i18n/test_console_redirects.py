"""Console redirects must name a path, never an origin.

The bug this guards against was found by an accessibility audit that appeared
to pass. `new URL("/", request.url)` builds an *absolute* redirect, and in the
standalone server `request.url` carries the bind address — so a console reached
on 127.0.0.1 sent the browser to `http://0.0.0.0:3000/`. Different origin, so
the session cookie set on that very response was not sent with the follow-up
request, and the user bounced back to the sign-in page. Behind a reverse proxy
the same construction takes its host from whatever upstream passes along.

A path-only Location is resolved by the client against the request it actually
made, and cannot name another origin at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

ROUTES = sorted((Path(__file__).resolve().parents[2] / "apps/web/app/api").rglob("route.ts"))

#: The two constructions that reintroduce an absolute redirect.
ABSOLUTE_REDIRECT = re.compile(r"NextResponse\.redirect\s*\(|new URL\s*\(\s*[`\"']")


def test_there_are_route_handlers_to_check() -> None:
    """A guard that finds nothing to guard is not a guard."""
    assert len(ROUTES) >= 5, f"expected the console's API routes, found {len(ROUTES)}"


@pytest.mark.parametrize("route", ROUTES, ids=lambda p: str(p.relative_to(p.parents[4])))
def test_a_route_does_not_build_an_absolute_redirect(route: Path) -> None:
    source = _without_comments(route.read_text(encoding="utf-8"))
    offenders = [
        f"line {source.count(chr(10), 0, m.start()) + 1}: {m.group().strip()}"
        for m in ABSOLUTE_REDIRECT.finditer(source)
    ]
    assert offenders == [], (
        f"{route.name} builds a redirect from the request's own URL, whose host is "
        f"the bind address rather than the host the client used. Use redirectTo() "
        f"from lib/redirect.ts instead. Offending lines: {offenders}"
    )


def _without_comments(source: str) -> str:
    """Blank out comments, preserving line numbers.

    The helper's own docstring names the construction it exists to replace, so
    a naive substring search flags the explanation rather than any code.
    """
    source = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group()), source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def test_the_helper_emits_a_relative_location() -> None:
    """The helper itself must not quietly grow an absolute form."""
    path = Path(__file__).resolve().parents[2] / "apps/web/lib/redirect.ts"
    code = _without_comments(path.read_text(encoding="utf-8"))
    assert "headers: { location }" in code, "the helper no longer sets a bare Location"
    assert "NextResponse.redirect" not in code
    assert "request.url" not in code
