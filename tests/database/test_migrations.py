"""Migration history is forward-only and checksum-verified."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core import migrate
from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


def test_all_migrations_are_applied(db: Session) -> None:
    applied = {
        r[0] for r in db.execute(text("SELECT version FROM schema_migrations")).all()
    }
    on_disk = {p.stem for p in migrate.discover()}
    assert on_disk, "expected migration files on disk"
    assert on_disk <= applied, f"unapplied migrations: {sorted(on_disk - applied)}"


def test_running_migrations_again_is_a_no_op() -> None:
    assert migrate.migrate() == []


def test_modified_migration_is_rejected(tmp_path: Path) -> None:
    """Editing an applied migration must raise rather than silently re-running."""
    from sqlalchemy import text as sql

    from agentic_os.core.db import get_owner_engine

    directory = tmp_path / "migrations"
    directory.mkdir()
    migration = directory / "9001_probe.sql"
    migration.write_text("SELECT 1;", encoding="utf-8")

    try:
        applied = migrate.migrate(directory)
        assert applied == ["9001_probe"]

        migration.write_text("SELECT 2;  -- edited after the fact", encoding="utf-8")
        with pytest.raises(RuntimeError) as excinfo:
            migrate.migrate(directory)
        assert "modified after being applied" in str(excinfo.value)
    finally:
        with get_owner_engine().begin() as conn:
            conn.execute(
                sql("DELETE FROM schema_migrations WHERE version = '9001_probe'")
            )


def test_migration_status_reports_every_file() -> None:
    status = dict(migrate.status())
    assert status, "status must list the migrations"
    assert all(state in ("applied", "pending") for state in status.values())


def test_checksums_are_recorded_for_every_applied_migration(db: Session) -> None:
    rows = db.execute(
        text("SELECT version, checksum FROM schema_migrations ORDER BY version")
    ).all()
    assert rows
    for row in rows:
        assert len(row.checksum) == 64, f"{row.version} has no sha256 checksum"
