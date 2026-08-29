"""The decision lifecycle, asserted exhaustively rather than by sampling.

Every ordered pair of the eleven states is checked — 121 assertions — so a
transition cannot be quietly added or removed. Sampling the interesting cases
would pass just as happily against a graph that had lost an edge.
"""

from __future__ import annotations

import uuid

import pytest
from agentic_os.core.context import ExecutionContext, HumanIdentity
from agentic_os.core.errors import AuthorizationError, NotFound
from agentic_os.decisions.lifecycle import (
    LEGAL_TRANSITIONS,
    MFA_REQUIRED,
    STATES,
    IllegalTransition,
    create_decision,
    is_legal,
    transition,
)
from sqlalchemy import text

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

ALL_PERMISSIONS = frozenset({"*"})

#: The happy path, start to finish. Written out rather than derived from the
#: graph so that a mistake in the graph cannot make the test agree with it.
FULL_LOOP = [
    "ANALYSING",
    "RECOMMENDATION_READY",
    "AWAITING_REVIEW",
    "AWAITING_APPROVAL",
    "APPROVED",
    "EXECUTING",
    "VERIFICATION_PENDING",
    "VERIFIED",
    "CLOSED",
]


def _ctx(tenant_id: str, organization_id: str, user_id: str, **kwargs) -> ExecutionContext:
    human = HumanIdentity(
        user_id=user_id,
        email="lifecycle@rta.example",
        permissions=kwargs.pop("permissions", ALL_PERMISSIONS),
        roles=kwargs.pop("roles", frozenset({"department_manager"})),
        mfa_satisfied=kwargs.pop("mfa", True),
    )
    return ExecutionContext(tenant_id=tenant_id, organization_id=organization_id, human=human)


@pytest.fixture()
def case(db, tenant_id, organization_id, seeded):
    """A fresh decision in DETECTED, owned by a fully privileged principal."""
    user_id = str(
        db.execute(
            text("SELECT id FROM users WHERE tenant_id = CAST(:t AS uuid) LIMIT 1"),
            {"t": tenant_id},
        ).scalar_one()
    )
    slug = f"life-{uuid.uuid4().hex[:8]}"
    domain_id = str(
        db.execute(
            text(
                "INSERT INTO domains (tenant_id, slug, name) VALUES (CAST(:t AS uuid), :s, 'Lifecycle') "
                "RETURNING id"
            ),
            {"t": tenant_id, "s": slug},
        ).scalar_one()
    )
    ctx = _ctx(tenant_id, organization_id, user_id)
    decision_id = create_decision(
        db,
        ctx,
        domain_id=domain_id,
        reference=f"LIFE-{slug}",
        title="Replace the failing point machine at Al Rashidiya",
    )
    db.flush()
    return {"ctx": ctx, "id": decision_id, "domain_id": domain_id, "user_id": user_id}


def _state(db, decision_id: str) -> str:
    return str(
        db.execute(
            text("SELECT state FROM decisions WHERE id = CAST(:d AS uuid)"), {"d": decision_id}
        ).scalar_one()
    )


# ------------------------------------------------------------------- the graph
def test_the_transition_table_covers_every_state() -> None:
    assert set(LEGAL_TRANSITIONS) == set(STATES)
    for state, destinations in LEGAL_TRANSITIONS.items():
        unknown = destinations - set(STATES)
        assert not unknown, f"{state} points at states that do not exist: {unknown}"


def test_every_ordered_pair_of_states_is_legal_or_illegal_as_declared() -> None:
    """All 121 pairs, so an edge cannot appear or vanish unnoticed."""
    checked = 0
    for source in STATES:
        for target in STATES:
            expected = target in LEGAL_TRANSITIONS[source]
            assert is_legal(source, target) is expected, f"{source} -> {target}"
            checked += 1
    assert checked == len(STATES) ** 2 == 121


def test_closed_is_terminal() -> None:
    assert LEGAL_TRANSITIONS["CLOSED"] == frozenset()


def test_every_state_can_reach_a_terminal_state() -> None:
    """No state may be a trap: a case must always be closable."""
    reachable = {"CLOSED"}
    changed = True
    while changed:
        changed = False
        for source, destinations in LEGAL_TRANSITIONS.items():
            if source not in reachable and destinations & reachable:
                reachable.add(source)
                changed = True
    stranded = set(STATES) - reachable
    assert not stranded, f"these states cannot reach CLOSED: {stranded}"


# ------------------------------------------------------------- the happy path
def test_a_decision_runs_the_whole_loop(db, case) -> None:
    assert _state(db, case["id"]) == "DETECTED"
    for target in FULL_LOOP:
        result = transition(db, case["ctx"], decision_id=case["id"], to_state=target)
        assert result.to_state == target
        assert _state(db, case["id"]) == target
    db.flush()


def test_every_move_is_recorded_in_the_append_only_log(db, case) -> None:
    for target in FULL_LOOP[:4]:
        transition(db, case["ctx"], decision_id=case["id"], to_state=target)
    db.flush()
    logged = [
        (r["from_state"], r["to_state"])
        for r in db.execute(
            text(
                "SELECT from_state, to_state FROM decision_transitions "
                "WHERE decision_id = CAST(:d AS uuid) ORDER BY occurred_at"
            ),
            {"d": case["id"]},
        ).mappings()
    ]
    assert logged[0] == (None, "DETECTED"), "creation must appear in the history"
    assert [t for _, t in logged[1:]] == FULL_LOOP[:4]


def test_closing_stamps_the_closure_time(db, case) -> None:
    transition(db, case["ctx"], decision_id=case["id"], to_state="CLOSED")
    db.flush()
    closed_at = db.execute(
        text("SELECT closed_at FROM decisions WHERE id = CAST(:d AS uuid)"), {"d": case["id"]}
    ).scalar_one()
    assert closed_at is not None


# ------------------------------------------------------------------- refusals
def test_an_illegal_transition_is_refused(db, case) -> None:
    """The move the brief specifically warns about: straight to Verified."""
    with pytest.raises(IllegalTransition) as excinfo:
        transition(db, case["ctx"], decision_id=case["id"], to_state="VERIFIED")
    assert "DETECTED" in str(excinfo.value)


def test_the_state_is_unchanged_after_a_refused_transition(db, case) -> None:
    with pytest.raises(IllegalTransition):
        transition(db, case["ctx"], decision_id=case["id"], to_state="APPROVED")
    assert _state(db, case["id"]) == "DETECTED"


def test_an_unknown_state_is_refused(db, case) -> None:
    with pytest.raises(IllegalTransition):
        transition(db, case["ctx"], decision_id=case["id"], to_state="TOTALLY_FINE")  # type: ignore[arg-type]


def test_a_missing_decision_is_reported_as_not_found(db, case) -> None:
    with pytest.raises(NotFound):
        transition(db, case["ctx"], decision_id=str(uuid.uuid4()), to_state="ANALYSING")


# -------------------------------------------------------------- authorization
def test_approval_requires_the_approval_permission(db, case, tenant_id, organization_id) -> None:
    reviewer = _ctx(
        tenant_id,
        organization_id,
        case["user_id"],
        permissions=frozenset({"decisions:analyse", "decisions:review"}),
    )
    for target in ["ANALYSING", "RECOMMENDATION_READY", "AWAITING_REVIEW", "AWAITING_APPROVAL"]:
        transition(db, reviewer, decision_id=case["id"], to_state=target)
    with pytest.raises(AuthorizationError) as excinfo:
        transition(db, reviewer, decision_id=case["id"], to_state="APPROVED")
    assert "decisions:approve" in str(excinfo.value)


def test_a_section_lead_can_review_but_cannot_approve(db, case, tenant_id, organization_id) -> None:
    """REVIEW and APPROVE are separate stations; a role holding both collapses them."""
    from agentic_os.identity.permissions import SYSTEM_ROLES_BY_SLUG

    lead = SYSTEM_ROLES_BY_SLUG["section_lead"]
    assert "decisions:review" in lead.permissions
    assert "decisions:approve" not in lead.permissions

    ctx = _ctx(tenant_id, organization_id, case["user_id"], permissions=frozenset(lead.permissions))
    for target in ["ANALYSING", "RECOMMENDATION_READY", "AWAITING_REVIEW", "AWAITING_APPROVAL"]:
        transition(db, ctx, decision_id=case["id"], to_state=target)
    with pytest.raises(AuthorizationError):
        transition(db, ctx, decision_id=case["id"], to_state="APPROVED")


@pytest.mark.parametrize("state", sorted(MFA_REQUIRED))
def test_the_committing_moves_require_a_second_factor(
    db, case, tenant_id, organization_id, state: str
) -> None:
    no_mfa = _ctx(tenant_id, organization_id, case["user_id"], mfa=False)
    path = FULL_LOOP[: FULL_LOOP.index(state)]
    with_mfa = case["ctx"]
    for target in path:
        transition(db, with_mfa, decision_id=case["id"], to_state=target)
    with pytest.raises(AuthorizationError) as excinfo:
        transition(db, no_mfa, decision_id=case["id"], to_state=state)
    assert "MFA" in str(excinfo.value)


def test_an_agent_cannot_approve_or_verify_on_its_own(db, case, tenant_id, organization_id) -> None:
    """The constitution's shape: the machine prepares, a person commits."""
    from agentic_os.core.context import AgentIdentity

    agent_ctx = ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=None,
        agent=AgentIdentity(agent_id=str(uuid.uuid4()), agent_version="1.0.0", autonomy_level="A2"),
    )
    # An agent may advance its own analysis.
    transition(db, agent_ctx, decision_id=case["id"], to_state="ANALYSING")
    transition(db, agent_ctx, decision_id=case["id"], to_state="RECOMMENDATION_READY")
    transition(db, agent_ctx, decision_id=case["id"], to_state="AWAITING_REVIEW")
    # It may not carry the case past a human station.
    with pytest.raises(AuthorizationError) as excinfo:
        transition(db, agent_ctx, decision_id=case["id"], to_state="AWAITING_APPROVAL")
    assert "human" in str(excinfo.value).lower()


# ------------------------------------------------------------- one writer only
def test_nothing_outside_the_lifecycle_module_writes_the_state_column() -> None:
    """A state machine spread across call sites is not a state machine.

    Scans the package source rather than trusting the convention, because the
    convention is exactly what a future change would breach.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "packages/agentic_os/src/agentic_os"
    writer = root / "decisions" / "lifecycle.py"
    pattern = re.compile(r"UPDATE\s+decisions\b", re.IGNORECASE)

    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if path != writer and pattern.search(path.read_text())
    ]
    assert offenders == [], f"these modules write decisions.state directly: {offenders}"
