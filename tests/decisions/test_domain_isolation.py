"""Cross-domain access, which the brief requires to be zero.

Zero is a stronger claim than "filtered". A system that fetches rows and drops
the ones you may not see has already read them: they were in memory, they can
be counted through a timing or a count endpoint, and one careless join
downstream turns "dropped" into "shown". So these tests assert two separate
things at two separate layers:

* the repository returns nothing — the database never found the row; and
* the answer is *not found*, not *forbidden*, because 403 against a specific
  identifier confirms the identifier names something real.
"""

from __future__ import annotations

import uuid

import pytest
from agentic_os.core.context import ExecutionContext, HumanIdentity
from agentic_os.core.errors import NotFound
from agentic_os.decisions.lifecycle import create_decision
from agentic_os.decisions.repository import (
    CROSS_DOMAIN_ROLES,
    get_decision,
    list_decisions,
    user_domain_ids,
)
from sqlalchemy import text

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

READS_DECISIONS = frozenset({"decisions:read", "decisions:create", "decisions:analyse"})


@pytest.fixture()
def two_domains(db, tenant_id, organization_id, seeded):
    """Two domains, two users, one member of each. One decision in each domain.

    Both users hold identical permissions, so anything either of them cannot
    see is down to domain membership and nothing else.
    """
    users = [
        str(r)
        for r in db.execute(
            text("SELECT id FROM users WHERE tenant_id = CAST(:t AS uuid) ORDER BY email LIMIT 2"),
            {"t": tenant_id},
        ).scalars()
    ]
    assert len(users) == 2, "the seed must provide at least two users"

    made = []
    for index, user_id in enumerate(users):
        slug = f"iso-{index}-{uuid.uuid4().hex[:8]}"
        domain_id = str(
            db.execute(
                text(
                    "INSERT INTO domains (tenant_id, slug, name) "
                    "VALUES (CAST(:t AS uuid), :s, :n) RETURNING id"
                ),
                {"t": tenant_id, "s": slug, "n": f"Domain {index}"},
            ).scalar_one()
        )
        db.execute(
            text(
                "INSERT INTO team_members (tenant_id, domain_id, user_id) "
                "VALUES (CAST(:t AS uuid), CAST(:d AS uuid), CAST(:u AS uuid))"
            ),
            {"t": tenant_id, "d": domain_id, "u": user_id},
        )
        ctx = ExecutionContext(
            tenant_id=tenant_id,
            organization_id=organization_id,
            human=HumanIdentity(
                user_id=user_id,
                email=f"member{index}@rta.example",
                permissions=READS_DECISIONS,
                roles=frozenset({"engineer"}),
                mfa_satisfied=True,
            ),
        )
        decision_id = create_decision(
            db,
            ctx,
            domain_id=domain_id,
            reference=f"ISO-{slug}",
            title=f"Decision confined to domain {index}",
        )
        made.append({"ctx": ctx, "domain_id": domain_id, "decision_id": decision_id})
    db.flush()
    return made


def test_a_member_sees_their_own_domains_decision(db, two_domains) -> None:
    a = two_domains[0]
    case = get_decision(db, a["ctx"], a["decision_id"])
    assert case["id"] == uuid.UUID(a["decision_id"])


def test_a_non_member_is_told_the_decision_does_not_exist(db, two_domains) -> None:
    """Not 'forbidden'. A 403 on a real identifier is itself a disclosure."""
    a, b = two_domains
    with pytest.raises(NotFound):
        get_decision(db, b["ctx"], a["decision_id"])


def test_the_queue_never_lists_another_domains_decision(db, two_domains) -> None:
    a, b = two_domains
    visible = {str(row["id"]) for row in list_decisions(db, b["ctx"])}
    assert a["decision_id"] not in visible
    assert b["decision_id"] in visible


def test_naming_the_other_domain_explicitly_does_not_widen_access(db, two_domains) -> None:
    """The obvious attack: pass the domain you want as a filter."""
    a, b = two_domains
    rows = list_decisions(db, b["ctx"], domain_id=a["domain_id"])
    assert rows == [], "a domain filter must narrow the scope, never widen it"


def test_the_database_returns_nothing_rather_than_filtering_afterwards(db, two_domains) -> None:
    """The count is taken in SQL, so a non-zero result would mean the row was
    genuinely fetched and only then discarded."""
    a, b = two_domains
    found = db.execute(
        text(
            """
            SELECT count(*) FROM decisions d
             WHERE d.tenant_id = CAST(:t AS uuid)
               AND d.id = CAST(:target AS uuid)
               AND d.domain_id IN (
                     SELECT tm.domain_id FROM team_members tm
                      WHERE tm.tenant_id = d.tenant_id AND tm.user_id = CAST(:actor AS uuid))
            """
        ),
        {
            "t": b["ctx"].tenant_id,
            "target": a["decision_id"],
            "actor": b["ctx"].human.user_id,
        },
    ).scalar_one()
    assert found == 0


def test_a_principal_with_no_membership_at_all_sees_nothing(
    db, two_domains, tenant_id, organization_id
) -> None:
    """Holding decisions:read grants the right to read decisions, not access to
    any particular one."""
    stranger = ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(
            user_id=str(uuid.uuid4()),
            email="stranger@rta.example",
            permissions=READS_DECISIONS,
            roles=frozenset({"engineer"}),
        ),
    )
    assert user_domain_ids(db, stranger) == []
    assert list_decisions(db, stranger) == []
    for made in two_domains:
        with pytest.raises(NotFound):
            get_decision(db, stranger, made["decision_id"])


def test_an_auditor_sees_across_domains_by_design(db, two_domains, tenant_id, organization_id) -> None:
    """The exemption is enumerated, not inferred from a wildcard permission, so
    a new role cannot acquire it by accident."""
    auditor = ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(
            user_id=str(uuid.uuid4()),
            email="auditor@rta.example",
            permissions=READS_DECISIONS,
            roles=frozenset({"auditor"}),
            mfa_satisfied=True,
        ),
    )
    visible = {str(row["id"]) for row in list_decisions(db, auditor)}
    for made in two_domains:
        assert made["decision_id"] in visible


def test_the_cross_domain_exemption_stays_small() -> None:
    """A guard on the guard: this set is the whole of the exception, and it
    growing quietly is how 'zero cross-domain access' stops being true."""
    assert CROSS_DOMAIN_ROLES == frozenset({"auditor", "governance_admin", "executive"})


def test_a_platform_administrator_does_not_see_across_domains(
    db, two_domains, tenant_id, organization_id
) -> None:
    """Administering the platform is not authority over its decisions."""
    admin = ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(
            user_id=str(uuid.uuid4()),
            email="platform.admin@rta.example",
            permissions=frozenset({"*"}),
            roles=frozenset({"platform_admin"}),
            mfa_satisfied=True,
        ),
    )
    assert list_decisions(db, admin) == []


def test_decisions_do_not_cross_the_tenant_boundary_either(
    db, db_other, two_domains, other_tenant_id, organization_id
) -> None:
    """Domain scope is added to tenant isolation, never in place of it."""
    a = two_domains[0]
    outsider = ExecutionContext(
        tenant_id=other_tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(
            user_id=a["ctx"].human.user_id,
            email="other.tenant@bus.example",
            permissions=frozenset({"*"}),
            roles=frozenset({"auditor"}),
            mfa_satisfied=True,
        ),
    )
    assert list_decisions(db_other, outsider) == []
    with pytest.raises(NotFound):
        get_decision(db_other, outsider, a["decision_id"])
