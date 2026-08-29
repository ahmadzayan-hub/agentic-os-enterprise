"""Every foreign key on a decision-intelligence table has a supporting index.

PostgreSQL indexes the *referenced* side of a foreign key automatically and the
*referencing* side not at all. Every DELETE on a parent row therefore makes the
planner prove no child references it, and without an index that proof is a
sequential scan of the child table — once per foreign key, per row deleted.

Measured on this hardware at 200,000 transition rows, deleting one user that
references nothing: 1180 ms cold and 15.2 ms warm without the indexes, 7.7 ms
and 3.5 ms with them. Tenant retirement walks exactly that path.

This test exists because the gap is invisible: nothing fails, queries stay
correct, and the cost only shows up as an offboarding that times out on a
database large enough to matter.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

#: The tables migration 0013 added. Scoped deliberately: the pre-existing
#: schema has its own history and its own trade-offs, and widening this test to
#: cover it would be a separate piece of work with its own measurements.
DECISION_TABLES = (
    "domains",
    "teams",
    "team_members",
    "decisions",
    "decision_options",
    "recommendations",
    "decision_evidence",
    "decision_transitions",
    "actions",
    "kpi_definitions",
    "kpi_values",
    "decision_outcomes",
    "lessons_learned",
    "notifications",
    "policy_results",
)

UNINDEXED_FOREIGN_KEYS = text(
    """
    SELECT c.conrelid::regclass::text AS child,
           a.attname                  AS column_name,
           c.confrelid::regclass::text AS parent
      FROM pg_constraint c
      JOIN pg_attribute a
        ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
     WHERE c.contype = 'f'
       AND c.conrelid::regclass::text = ANY(:tables)
       AND NOT EXISTS (
             SELECT 1 FROM pg_index i
              WHERE i.indrelid = c.conrelid
                AND a.attnum = ANY(i.indkey::smallint[]))
     ORDER BY 1, 2
    """
)


def test_every_foreign_key_is_indexed(provisioning_db, seeded) -> None:
    offenders = [
        f"{r['child']}.{r['column_name']} -> {r['parent']}"
        for r in provisioning_db.execute(UNINDEXED_FOREIGN_KEYS, {"tables": list(DECISION_TABLES)}).mappings()
    ]
    assert offenders == [], (
        "these foreign keys have no supporting index, so deleting a parent row "
        "sequentially scans the child table: " + ", ".join(offenders)
    )


def test_the_scan_covers_the_tables_it_claims_to(provisioning_db, seeded) -> None:
    """A guard on the guard.

    If a table name here stopped matching a real table — renamed, or misspelt
    from the start — the query above would return nothing and the test would
    pass while checking nothing at all.
    """
    present = {
        str(r)
        for r in provisioning_db.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(:t)"),
            {"t": list(DECISION_TABLES)},
        ).scalars()
    }
    missing = set(DECISION_TABLES) - present
    assert missing == set(), f"named tables that do not exist: {sorted(missing)}"


def test_the_query_would_notice_an_unindexed_key(provisioning_db, seeded) -> None:
    """Prove the detection works, rather than trusting an empty result.

    A temporary table with a deliberately unindexed foreign key must be found.
    Without this, an empty offender list is equally consistent with "all keys
    are indexed" and "the query is broken".
    """
    # Both tables are temporary: PostgreSQL refuses a constraint from a
    # temporary table to a permanent one, and a permanent probe table would
    # outlive a failed test.
    provisioning_db.execute(
        text("CREATE TEMP TABLE probe_parent (id uuid PRIMARY KEY DEFAULT gen_random_uuid())")
    )
    provisioning_db.execute(
        text(
            "CREATE TEMP TABLE probe_child ("
            "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            "  parent_id uuid REFERENCES probe_parent(id))"
        )
    )
    found = [
        f"{r['child']}.{r['column_name']}"
        for r in provisioning_db.execute(UNINDEXED_FOREIGN_KEYS, {"tables": ["probe_child"]}).mappings()
    ]
    provisioning_db.execute(text("DROP TABLE probe_child"))
    provisioning_db.execute(text("DROP TABLE probe_parent"))
    assert found == ["probe_child.parent_id"], (
        "the scan failed to notice a foreign key with no index, so a clean result from it means nothing"
    )
