"""Backup and restore exercise.

This module does not simulate disaster recovery. It takes a real ``pg_dump``
of the live database, restores it into a scratch database, and verifies the
restored copy before recording the result. A ``restore_tests`` row is written
only from measured numbers:

* **RTO achieved** is wall-clock seconds from the start of the restore to the
  moment the restored copy passes verification.
* **RPO achieved** is the data-loss window: seconds between the newest audit
  event present in the restored copy and the newest audit event in the source
  at verification time.

Verification is a comparison of every table's row count between source and
restored copy, plus a recomputation of the audit hash chain inside the
restored database. A copy that restores but whose ledger does not verify is
recorded as ``PARTIAL`` — restoring bytes is not the same as recovering a
trustworthy system.

The exercise needs to create and drop a database and to install extensions,
which neither the application role nor the migration owner may do. It
therefore takes a separate maintenance DSN from ``AGENTIC_DR_ADMIN_URL``. When
that is unset the exercise refuses to run rather than producing evidence of
something that did not happen.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from agentic_os.core.config import get_settings
from agentic_os.core.db import provisioning_session_scope
from agentic_os.core.errors import NotImplementedCapability
from agentic_os.core.ids import utcnow

#: Scratch database names are generated, never taken from input, and must match
#: this shape before they are interpolated into a CREATE/DROP statement.
_SAFE_DB_NAME = re.compile(r"\Aagentic_restore_[0-9]{14}_[0-9a-f]{8}\Z")

#: Tables excluded from the row-count comparison because the exercise itself
#: writes to them between the dump and the verification.
_VOLATILE_TABLES = frozenset({"backup_records", "restore_tests", "audit_events"})


class RestoreNotConfigured(NotImplementedCapability):
    """Raised when the exercise cannot run for want of configuration.

    Deliberately not swallowed: an unrunnable exercise leaves the control
    NOT_EVIDENCED, which is the honest outcome.
    """


@dataclass(slots=True)
class ExerciseResult:
    outcome: str
    backup_id: str | None
    restore_test_id: str | None
    artifact_path: str
    artifact_hash: str
    size_bytes: int
    backup_seconds: float
    rto_seconds: int | None
    rpo_seconds: int | None
    verified_rows: int
    tables_compared: int
    mismatches: dict[str, dict[str, int]] = field(default_factory=dict)
    ledger: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "backup_id": self.backup_id,
            "restore_test_id": self.restore_test_id,
            "artifact_path": self.artifact_path,
            "artifact_hash": self.artifact_hash,
            "size_bytes": self.size_bytes,
            "backup_seconds": round(self.backup_seconds, 3),
            "rto_seconds": self.rto_seconds,
            "rpo_seconds": self.rpo_seconds,
            "verified_rows": self.verified_rows,
            "tables_compared": self.tables_compared,
            "mismatches": self.mismatches,
            "ledger": self.ledger,
            "notes": self.notes,
        }


def _libpq(url: URL) -> tuple[list[str], dict[str, str]]:
    """Connection flags and environment for the libpq command line tools."""
    args: list[str] = []
    if url.host:
        args += ["-h", url.host]
    if url.port:
        args += ["-p", str(url.port)]
    if url.username:
        args += ["-U", url.username]
    env = dict(os.environ)
    if url.password:
        env["PGPASSWORD"] = url.password
    host = (url.query.get("host") if url.query else None) or None
    if isinstance(host, str):
        args += ["-h", host]
    return args, env


def _run(command: list[str], env: dict[str, str], *, what: str) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        command, env=env, capture_output=True, text=True, timeout=900
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{what} failed with exit code {completed.returncode}: {completed.stderr.strip()[:2000]}"
        )
    return completed.stdout


def _admin_url() -> URL:
    settings = get_settings()
    if not settings.dr_admin_url:
        raise RestoreNotConfigured(
            "AGENTIC_DR_ADMIN_URL is not set. The restore exercise needs a "
            "maintenance identity that may create and drop a scratch database; "
            "without it no restore evidence can be produced."
        )
    return make_url(settings.dr_admin_url)


def _table_counts(engine_url: URL, *, as_provisioner: bool) -> dict[str, int]:
    """Row counts for every table in the public schema."""
    engine = create_engine(engine_url, future=True)
    try:
        with engine.connect() as conn:
            if as_provisioner:
                conn.execute(text("SET ROLE agentic_provisioner"))
            names = [
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                        "ORDER BY table_name"
                    )
                )
            ]
            counts: dict[str, int] = {}
            for name in names:
                if not name.isidentifier():  # pragma: no cover - defensive
                    continue
                counts[name] = int(
                    conn.execute(text(f'SELECT count(*) FROM public."{name}"')).scalar_one()  # noqa: S608
                )
            return counts
    finally:
        engine.dispose()


def _latest_audit_time(engine_url: URL, *, as_provisioner: bool) -> datetime | None:
    engine = create_engine(engine_url, future=True)
    try:
        with engine.connect() as conn:
            if as_provisioner:
                conn.execute(text("SET ROLE agentic_provisioner"))
            return conn.execute(text("SELECT max(occurred_at) FROM audit_events")).scalar()
    finally:
        engine.dispose()


def _verify_ledger(engine_url: URL) -> dict[str, Any]:
    """Recompute every tenant's audit chain inside the restored database."""
    engine = create_engine(engine_url, future=True)
    try:
        with engine.connect() as conn:
            tenants = [str(r[0]) for r in conn.execute(text("SELECT id FROM tenants"))]
            checked = 0
            broken: list[str] = []
            for tenant_id in tenants:
                row = conn.execute(
                    text("SELECT checked, broken_at FROM audit_verify_chain(CAST(:t AS uuid))"),
                    {"t": tenant_id},
                ).one()
                checked += int(row.checked)
                if row.broken_at is not None:
                    broken.append(f"{tenant_id}@{int(row.broken_at)}")
            return {
                "tenants": len(tenants),
                "entries_checked": checked,
                "intact": not broken,
                "broken_at": broken,
            }
    finally:
        engine.dispose()


def run_exercise(
    *,
    environment: str = "test",
    executed_by: str = "",
    keep_artifact: bool = True,
) -> ExerciseResult:
    """Dump, restore, verify and record. Returns the measured result."""
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
        raise RestoreNotConfigured("pg_dump and pg_restore must be on PATH")

    settings = get_settings()
    admin = _admin_url()
    source = make_url(settings.database_owner_url)
    source_db = source.database
    if not source_db:
        raise RestoreNotConfigured("the owner DSN names no database")

    artifact_dir = Path(settings.dr_artifact_path)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    scratch = f"agentic_restore_{stamp}_{os.urandom(4).hex()}"
    if not _SAFE_DB_NAME.match(scratch):  # pragma: no cover - generated above
        raise RuntimeError("generated scratch database name failed validation")
    artifact = artifact_dir / f"{scratch}.dump"

    admin_args, admin_env = _libpq(admin)
    # The dump is taken with the maintenance identity, not the migration owner.
    # Every protected table declares FORCE ROW LEVEL SECURITY with no bypass
    # predicate, so the owner's own COPY is filtered by the tenant policy and
    # pg_dump aborts. A complete backup is therefore a privileged operation by
    # construction -- which is the intended consequence of the isolation model,
    # not a workaround for it.
    dump_args, dump_env = _libpq(admin)

    # --- recovery point -----------------------------------------------------
    # pg_dump takes a consistent snapshot at the moment it starts, so that is
    # the point the restored copy can recover to.
    snapshot_at = utcnow()

    started = time.monotonic()
    _run(
        ["pg_dump", *dump_args, "-d", source_db, "-Fc", "--no-password", "-f", str(artifact)],
        dump_env,
        what="pg_dump",
    )
    backup_seconds = time.monotonic() - started

    digest = hashlib.sha256()
    with artifact.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    artifact_hash = digest.hexdigest()
    size_bytes = artifact.stat().st_size

    with provisioning_session_scope() as session:
        backup_id = str(
            session.execute(
                text(
                    """
                    INSERT INTO backup_records (tenant_id, backup_type, scope, artifact_uri,
                                                artifact_hash, size_bytes, status,
                                                started_at, completed_at)
                    VALUES (NULL, 'DATABASE', 'full', :uri, :hash, :size, 'COMPLETED',
                            :started, now())
                    RETURNING id
                    """
                ),
                {
                    "uri": str(artifact),
                    "hash": artifact_hash,
                    "size": size_bytes,
                    "started": snapshot_at,
                },
            ).scalar_one()
        )

    source_counts = _table_counts(source, as_provisioner=True)

    # --- restore ------------------------------------------------------------
    restore_started = time.monotonic()
    restored_url = admin.set(database=scratch)
    outcome = "FAILURE"
    notes = ""
    mismatches: dict[str, dict[str, int]] = {}
    ledger: dict[str, Any] = {}
    verified_rows = 0
    rto_seconds: int | None = None
    rpo_seconds: int | None = None
    tables_compared = 0

    try:
        _run(
            [
                "psql",
                *admin_args,
                "-d",
                admin.database or "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                f'CREATE DATABASE "{scratch}"',
            ],
            admin_env,
            what="CREATE DATABASE",
        )
        _run(
            [
                "pg_restore",
                *admin_args,
                "-d",
                scratch,
                "--no-owner",
                "--no-password",
                "--exit-on-error",
                str(artifact),
            ],
            admin_env,
            what="pg_restore",
        )

        restored_counts = _table_counts(restored_url, as_provisioner=False)
        for table, expected in source_counts.items():
            if table in _VOLATILE_TABLES:
                continue
            tables_compared += 1
            found = restored_counts.get(table)
            if found is None or found != expected:
                mismatches[table] = {"source": expected, "restored": found or 0}
            else:
                verified_rows += found

        ledger = _verify_ledger(restored_url)

        rto_seconds = int(time.monotonic() - restore_started)
        source_latest = _latest_audit_time(source, as_provisioner=True)
        restored_latest = _latest_audit_time(restored_url, as_provisioner=False)
        if source_latest is not None and restored_latest is not None:
            rpo_seconds = max(0, int((source_latest - restored_latest).total_seconds()))

        if mismatches:
            outcome = "PARTIAL"
            notes = (
                f"{len(mismatches)} of {tables_compared} tables differ between source "
                f"and restored copy: {', '.join(sorted(mismatches))}"
            )
        elif not ledger.get("intact", False):
            outcome = "PARTIAL"
            notes = (
                "row counts matched but the audit chain did not verify in the "
                f"restored copy: {ledger.get('broken_at')}"
            )
        else:
            outcome = "SUCCESS"
            notes = (
                f"pg_dump/pg_restore round trip verified: {tables_compared} tables and "
                f"{verified_rows} rows matched, and {ledger['entries_checked']} audit "
                f"entries across {ledger['tenants']} tenants re-hashed intact in the "
                "restored database"
            )
    except Exception as exc:  # noqa: BLE001 - recorded as failure evidence
        notes = f"restore exercise failed: {exc}"[:2000]
    finally:
        _drop_database(admin, admin_args, admin_env, scratch)
        if not keep_artifact:
            artifact.unlink(missing_ok=True)

    with provisioning_session_scope() as session:
        restore_test_id = str(
            session.execute(
                text(
                    """
                    INSERT INTO restore_tests (tenant_id, backup_id, environment, outcome,
                                               rpo_achieved_seconds, rto_achieved_seconds,
                                               verified_rows, notes, executed_by)
                    VALUES (NULL, CAST(:backup AS uuid), :env, :outcome, :rpo, :rto,
                            :rows, :notes, :by)
                    RETURNING id
                    """
                ),
                {
                    "backup": backup_id,
                    "env": environment,
                    "outcome": outcome,
                    "rpo": rpo_seconds,
                    "rto": rto_seconds,
                    "rows": verified_rows,
                    "notes": notes,
                    "by": executed_by or "agentic-dr-exercise",
                },
            ).scalar_one()
        )

    return ExerciseResult(
        outcome=outcome,
        backup_id=backup_id,
        restore_test_id=restore_test_id,
        artifact_path=str(artifact),
        artifact_hash=artifact_hash,
        size_bytes=size_bytes,
        backup_seconds=backup_seconds,
        rto_seconds=rto_seconds,
        rpo_seconds=rpo_seconds,
        verified_rows=verified_rows,
        tables_compared=tables_compared,
        mismatches=mismatches,
        ledger=ledger,
        notes=notes,
    )


def _drop_database(admin: URL, args: list[str], env: dict[str, str], name: str) -> None:
    if not _SAFE_DB_NAME.match(name):  # pragma: no cover - defensive
        raise RuntimeError(f"refusing to drop database with unexpected name '{name}'")
    subprocess.run(  # noqa: S603, S607 - fixed argv, validated name, no shell
        [  # noqa: S607 - psql resolved from PATH by design
            "psql",
            *args,
            "-d",
            admin.database or "postgres",
            "-c",
            f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)',
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def latest_result() -> dict[str, Any] | None:
    """The most recent recorded exercise, for the operations surface."""
    with provisioning_session_scope() as session:
        row = (
            session.execute(
                text(
                    "SELECT outcome, rpo_achieved_seconds, rto_achieved_seconds, verified_rows, "
                    "notes, executed_by, executed_at FROM restore_tests "
                    "ORDER BY executed_at DESC LIMIT 1"
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None
