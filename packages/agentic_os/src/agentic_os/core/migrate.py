"""Forward-only SQL migration runner.

Migrations are plain ``.sql`` files applied in filename order inside a single
transaction each, with a recorded checksum. A previously applied file whose
content changed is a hard error: history is immutable, corrections ship as new
migrations.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

from sqlalchemy import text

from agentic_os.core.db import get_owner_engine

MIGRATIONS_DIR = Path("database/migrations")

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version      text PRIMARY KEY,
  checksum     text NOT NULL,
  applied_at   timestamptz NOT NULL DEFAULT now(),
  duration_ms  integer NOT NULL DEFAULT 0
);
"""


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def discover(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"migrations directory not found: {directory}")
    return sorted(p for p in directory.glob("*.sql") if not p.name.startswith("_"))


def applied_versions(engine) -> dict[str, str]:
    with engine.begin() as conn:
        conn.execute(text(_BOOTSTRAP))
        rows = conn.execute(text("SELECT version, checksum FROM schema_migrations")).all()
    return {row.version: row.checksum for row in rows}


def migrate(directory: Path = MIGRATIONS_DIR, *, dry_run: bool = False) -> list[str]:
    """Apply pending migrations. Returns the versions applied."""
    engine = get_owner_engine()
    already = applied_versions(engine)
    applied: list[str] = []

    for path in discover(directory):
        version = path.stem
        sql = path.read_text(encoding="utf-8")
        digest = _checksum(sql)

        if version in already:
            if already[version] != digest:
                raise RuntimeError(
                    f"migration {version} was modified after being applied "
                    f"(recorded {already[version][:12]}, file {digest[:12]}). "
                    "Ship a corrective migration instead of editing history."
                )
            continue

        if dry_run:
            applied.append(version)
            continue

        started = time.perf_counter()
        with engine.begin() as conn:
            conn.execute(text(sql))
            conn.execute(
                text(
                    "INSERT INTO schema_migrations (version, checksum, duration_ms) "
                    "VALUES (:v, :c, :d)"
                ),
                {
                    "v": version,
                    "c": digest,
                    "d": int((time.perf_counter() - started) * 1000),
                },
            )
        applied.append(version)

    return applied


def status(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, str]]:
    already = applied_versions(get_owner_engine())
    return [
        (p.stem, "applied" if p.stem in already else "pending") for p in discover(directory)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agentic OS migration runner")
    parser.add_argument("command", choices=["up", "status"], nargs="?", default="up")
    parser.add_argument("--dir", default=str(MIGRATIONS_DIR))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    directory = Path(args.dir)
    if args.command == "status":
        for version, state in status(directory):
            print(f"{state:>8}  {version}")
        return 0

    applied = migrate(directory, dry_run=args.dry_run)
    if not applied:
        print("database is up to date")
    for version in applied:
        print(f"{'would apply' if args.dry_run else 'applied'}  {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
