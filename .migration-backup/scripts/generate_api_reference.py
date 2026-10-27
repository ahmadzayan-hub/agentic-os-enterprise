"""Generate docs/api/API_REFERENCE.md from the live OpenAPI document.

Written from the application rather than by hand so it cannot drift: if an
endpoint is added, removed or has its required permission changed, regenerating
this file shows it.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "docs" / "api" / "API_REFERENCE.md"


def _permissions_by_operation() -> dict[tuple[str, str], str]:
    """Read the permission each route declares, from the route's dependencies.

    ``require_permission`` records the permission on the callable it returns,
    so this reads the real requirement rather than parsing source.
    """
    from agentic_os.api.app import API_PREFIX, create_app

    app = create_app()
    app.openapi()

    def walk(routes, mounted: bool = False):
        """Routers are included lazily, so descend into the wrappers.

        A route reached through an included router carries its path without the
        mount prefix; the OpenAPI document carries it with.
        """
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                yield from walk(included.routes, mounted=True)
            else:
                yield route, mounted

    found: dict[tuple[str, str], str] = {}
    for route, mounted in walk(app.routes):
        path = getattr(route, "path", "")
        if not path:
            continue
        full = API_PREFIX + path if mounted else path
        permission = ""
        for dependency in getattr(route, "dependencies", []) or []:
            call = getattr(dependency, "dependency", None)
            permission = getattr(call, "required_permission", "") or permission
        for sub in getattr(getattr(route, "dependant", None), "dependencies", []) or []:
            permission = getattr(sub.call, "required_permission", "") or permission
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            found[(method, full)] = permission
    return found


def main() -> int:
    from agentic_os.api.app import create_app

    app = create_app()
    spec = app.openapi()
    declared = _permissions_by_operation()

    by_tag: dict[str, list[tuple[str, str, str, str]]] = {}
    for path, operations in sorted(spec["paths"].items()):
        for method, operation in operations.items():
            tag = (operation.get("tags") or ["platform"])[0]
            summary = (operation.get("summary") or "").strip()
            description = (operation.get("description") or "").strip().splitlines()
            detail = summary or (description[0] if description else "")
            by_tag.setdefault(tag, []).append(
                (method.upper(), path, detail, declared.get((method.upper(), path), ""))
            )

    total = sum(len(rows) for rows in by_tag.values())
    lines = [
        "# API Reference",
        "",
        "Generated from the application's own OpenAPI document by",
        "`scripts/generate_api_reference.py`. Do not edit by hand.",
        "",
        f"**{total} endpoints** under `/api/v1`, plus `/health` and `/ready`.",
        "",
        "Authentication is a session cookie issued by `POST /api/v1/auth/login`;",
        "the cookie is httpOnly and the console exchanges it server-side, so the",
        "browser never holds a bearer token. Every endpoint below is authorised",
        "independently of the console: the permission column is what the route",
        "itself requires, and the request is refused without it whatever the",
        "caller's navigation shows.",
        "",
    ]
    for tag in sorted(by_tag):
        lines += [f"## {tag}", "", "| Method | Path | Permission | Purpose |", "|---|---|---|---|"]
        for method, path, detail, permission in sorted(by_tag[tag], key=lambda r: (r[1], r[0])):
            perm = f"`{permission}`" if permission else "—"
            lines.append(f"| {method} | `{path}` | {perm} | {detail} |")
        lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO_ROOT)} — {total} endpoints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
