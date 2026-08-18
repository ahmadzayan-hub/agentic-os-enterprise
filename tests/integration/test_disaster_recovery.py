"""Disaster recovery exercise against a real database.

The exercise is the evidence: it dumps the live database, restores it into a
scratch database, compares every table and re-hashes the audit chain in the
restored copy. Nothing here is simulated, so the test is skipped — leaving the
control NOT_EVIDENCED — when no maintenance identity is configured.
"""

from __future__ import annotations

import pytest
from agentic_os.core.config import get_settings
from agentic_os.resilience import RestoreNotConfigured, backup
from sqlalchemy import text

from tests.conftest import requires_db, requires_dr_identity

pytestmark = [pytest.mark.integration, requires_db]


def test_the_exercise_refuses_to_run_without_a_maintenance_identity(monkeypatch):
    """No configuration must mean no evidence, never fabricated evidence."""
    settings = get_settings()
    monkeypatch.setattr(settings, "dr_admin_url", "", raising=False)
    with pytest.raises(RestoreNotConfigured):
        backup.run_exercise(environment="test")


@requires_dr_identity
def test_a_restore_is_performed_and_verified_end_to_end(provisioning_db, seeded):
    """The control: a real restore, verified, with measured RPO and RTO."""
    result = backup.run_exercise(environment="test", executed_by="pytest", keep_artifact=False)

    assert result.outcome == "SUCCESS", result.notes
    assert result.mismatches == {}
    assert result.tables_compared > 20
    assert result.verified_rows > 0
    assert result.rto_seconds is not None and result.rto_seconds >= 0
    assert result.rpo_seconds is not None and result.rpo_seconds >= 0

    # The restored copy must be trustworthy, not merely present: every tenant's
    # hash chain is recomputed inside it.
    assert result.ledger["intact"] is True
    assert result.ledger["entries_checked"] > 0
    assert result.ledger["tenants"] > 0

    # The exercise leaves durable evidence behind.
    row = (
        provisioning_db.execute(
            text(
                "SELECT outcome, rpo_achieved_seconds, rto_achieved_seconds, verified_rows, notes "
                "FROM restore_tests WHERE id = CAST(:i AS uuid)"
            ),
            {"i": result.restore_test_id},
        )
        .mappings()
        .one()
    )
    assert row["outcome"] == "SUCCESS"
    assert row["rto_achieved_seconds"] is not None
    assert row["rpo_achieved_seconds"] is not None
    assert row["verified_rows"] == result.verified_rows

    backup_row = (
        provisioning_db.execute(
            text("SELECT status, artifact_hash, size_bytes FROM backup_records WHERE id = CAST(:i AS uuid)"),
            {"i": result.backup_id},
        )
        .mappings()
        .one()
    )
    assert backup_row["status"] == "COMPLETED"
    assert len(backup_row["artifact_hash"]) == 64
    assert backup_row["size_bytes"] > 0


@requires_dr_identity
def test_the_scratch_database_is_dropped_afterwards(provisioning_db):
    """A restore drill must not leave a second copy of production data behind."""
    result = backup.run_exercise(environment="test", executed_by="pytest", keep_artifact=False)
    leftovers = provisioning_db.execute(
        text("SELECT count(*) FROM pg_database WHERE datname LIKE 'agentic_restore_%'")
    ).scalar_one()
    assert leftovers == 0, "scratch restore databases were left behind"
    assert result.outcome == "SUCCESS"
