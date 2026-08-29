"""The decision lifecycle: eleven states and the only code allowed to move between them.

Why a single writer
-------------------
``decisions.state`` is written here and nowhere else. Not as a convention — a
test reads the source of every other module and fails if any of them writes the
column. The reason is that a state machine spread across a dozen call sites is
not a state machine, it is a set of assignments that happen to agree today.

Why the graph is data
---------------------
:data:`LEGAL_TRANSITIONS` is a table rather than a chain of ``if`` statements so
that the legal moves can be read, tested exhaustively (every ordered pair of
states is asserted legal or illegal — 121 assertions, no sampling) and rendered
in the UI without a second copy drifting out of step.

What a transition costs
-----------------------
Every transition writes three things atomically: the new state, an append-only
row in ``decision_transitions``, and an entry in the hash-chained audit ledger.
A transition that fails to record itself does not happen — the caller's
transaction is what commits, so partial history is impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.assurance.audit import AuditEntry, audit
from agentic_os.core.context import ExecutionContext
from agentic_os.core.db import affected_rows
from agentic_os.core.errors import AgenticError, AuthorizationError, ErrorClass, NotFound

DecisionState = Literal[
    "DETECTED",
    "ANALYSING",
    "RECOMMENDATION_READY",
    "AWAITING_REVIEW",
    "AWAITING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "EXECUTING",
    "VERIFICATION_PENDING",
    "VERIFIED",
    "CLOSED",
]

STATES: tuple[DecisionState, ...] = get_args(DecisionState)

#: The legal moves. Read this as the operating loop plus its escape hatches:
#: a reviewer may send a case back for more analysis, an approver may return it
#: for rework, and a case may be closed from any terminal position.
LEGAL_TRANSITIONS: dict[DecisionState, frozenset[DecisionState]] = {
    "DETECTED": frozenset({"ANALYSING", "CLOSED"}),
    "ANALYSING": frozenset({"RECOMMENDATION_READY", "CLOSED"}),
    "RECOMMENDATION_READY": frozenset({"AWAITING_REVIEW", "ANALYSING", "CLOSED"}),
    # A reviewer who cannot send work back is not reviewing, they are relaying.
    "AWAITING_REVIEW": frozenset({"AWAITING_APPROVAL", "ANALYSING", "REJECTED"}),
    "AWAITING_APPROVAL": frozenset({"APPROVED", "REJECTED", "AWAITING_REVIEW"}),
    "APPROVED": frozenset({"EXECUTING", "CLOSED"}),
    # Execution can still be refused: policy is evaluated at dispatch, not only
    # at approval, because the world may have changed in between.
    "EXECUTING": frozenset({"VERIFICATION_PENDING", "REJECTED"}),
    # CLOSED from here is the honest exit for an outcome nobody can measure.
    "VERIFICATION_PENDING": frozenset({"VERIFIED", "CLOSED"}),
    "VERIFIED": frozenset({"CLOSED"}),
    "REJECTED": frozenset({"CLOSED"}),
    "CLOSED": frozenset(),
}

#: The permission each destination demands. Absent from this map means the
#: transition is mechanical (the system advancing its own work) and carries the
#: originating permission instead.
TRANSITION_PERMISSION: dict[DecisionState, str] = {
    "ANALYSING": "decisions:analyse",
    "RECOMMENDATION_READY": "decisions:analyse",
    "AWAITING_REVIEW": "decisions:analyse",
    "AWAITING_APPROVAL": "decisions:review",
    "APPROVED": "decisions:approve",
    "REJECTED": "decisions:review",
    "EXECUTING": "decisions:execute",
    "VERIFICATION_PENDING": "decisions:execute",
    "VERIFIED": "decisions:verify",
    "CLOSED": "decisions:review",
}

#: Destinations that carry enough consequence to demand a second factor. These
#: are the two moments where a person commits the organisation to something.
MFA_REQUIRED: frozenset[DecisionState] = frozenset({"APPROVED", "EXECUTING"})

#: Who to tell, when. A pending approval that nobody is told about is
#: discovered by someone opening the console and looking, which is a habit
#: rather than a workflow.
NOTIFY_ON: dict[DecisionState, tuple[str, str]] = {
    "AWAITING_REVIEW": ("REVIEW_REQUESTED", "decisions:review"),
    "AWAITING_APPROVAL": ("APPROVAL_REQUESTED", "decisions:approve"),
    "APPROVED": ("DECISION_APPROVED", "decisions:execute"),
    "REJECTED": ("DECISION_REJECTED", "decisions:analyse"),
    "VERIFICATION_PENDING": ("VERIFICATION_DUE", "decisions:verify"),
}


class IllegalTransition(AgenticError):
    """A move the lifecycle does not permit.

    CONFLICT rather than VALIDATION: the request is well formed, it is the
    decision's current state that makes it wrong, and the caller may well
    succeed after refreshing.
    """

    error_class = ErrorClass.CONFLICT


@dataclass(slots=True)
class TransitionResult:
    decision_id: str
    from_state: DecisionState
    to_state: DecisionState
    notified: int


def is_legal(from_state: DecisionState, to_state: DecisionState) -> bool:
    return to_state in LEGAL_TRANSITIONS.get(from_state, frozenset())


def _require(ctx: ExecutionContext, permission: str, to_state: DecisionState) -> None:
    """Authorize server-side. The console hides what a caller cannot do; that
    is presentation. This is the enforcement, and it runs whether or not a
    console was involved."""
    human = ctx.human
    if human is None:
        # An agent may advance analysis but may never review, approve, execute
        # or verify. The constitution's rule that the conductor never executes
        # production tools has the same shape: the machine prepares, a person
        # commits.
        if to_state in {"AWAITING_APPROVAL", "APPROVED", "EXECUTING", "VERIFIED"}:
            raise AuthorizationError(f"moving a decision to {to_state} requires a human principal")
        return
    granted = human.permissions
    if "*" not in granted and permission not in granted:
        raise AuthorizationError(f"permission '{permission}' is required to reach {to_state}")
    if to_state in MFA_REQUIRED and not human.mfa_satisfied:
        raise AuthorizationError(f"reaching {to_state} requires a second factor; re-authenticate with MFA")


def create_decision(
    session: Session,
    ctx: ExecutionContext,
    *,
    domain_id: str,
    reference: str,
    title: str,
    summary: str = "",
    detected_by: str = "HUMAN",
    detection_source: str = "",
    classification: str = "INTERNAL",
    risk: str = "MEDIUM",
    owner_user_id: str | None = None,
    due_at: str | None = None,
) -> str:
    """Raise a decision case in DETECTED, and record how it came to exist."""
    _require(ctx, "decisions:create", "DETECTED")
    raised_by = ctx.human.user_id if ctx.human else None
    decision_id = str(
        session.execute(
            text(
                """
                INSERT INTO decisions (tenant_id, domain_id, reference, title, summary,
                                       detected_by, detection_source, classification, risk,
                                       owner_user_id, raised_by_user_id, run_id, due_at)
                VALUES (CAST(:t AS uuid), CAST(:dom AS uuid), :ref, :title, :summary,
                        :detected_by, :source, CAST(:cls AS data_classification),
                        CAST(:risk AS risk_class),
                        CAST(NULLIF(:owner, '') AS uuid), CAST(NULLIF(:raised, '') AS uuid),
                        CAST(NULLIF(:run, '') AS uuid), CAST(NULLIF(:due, '') AS timestamptz))
                RETURNING id
                """
            ),
            {
                "t": ctx.tenant_id,
                "dom": domain_id,
                "ref": reference,
                "title": title,
                "summary": summary,
                "detected_by": detected_by,
                "source": detection_source,
                "cls": classification,
                "risk": risk,
                "owner": owner_user_id or "",
                "raised": raised_by or "",
                "run": ctx.run_id or "",
                "due": due_at or "",
            },
        ).scalar_one()
    )

    session.execute(
        text(
            """
            INSERT INTO decision_transitions
                (tenant_id, decision_id, from_state, to_state, actor_user_id, actor_kind, reason)
            VALUES (CAST(:t AS uuid), CAST(:d AS uuid), NULL, 'DETECTED',
                    CAST(NULLIF(:actor, '') AS uuid), :kind, :reason)
            """
        ),
        {
            "t": ctx.tenant_id,
            "d": decision_id,
            "actor": raised_by or "",
            "kind": ctx.actor_type if ctx.actor_type in {"HUMAN", "AGENT"} else "SYSTEM",
            "reason": detection_source or "raised",
        },
    )

    audit(
        session,
        ctx,
        AuditEntry(
            category="USER_ACTION",
            action="decision.created",
            resource_type="decision",
            resource_id=decision_id,
            classification=classification,
            payload={"reference": reference, "domain_id": domain_id, "detected_by": detected_by},
        ),
    )
    return decision_id


def transition(
    session: Session,
    ctx: ExecutionContext,
    *,
    decision_id: str,
    to_state: DecisionState,
    reason: str = "",
) -> TransitionResult:
    """Move a decision, or refuse to.

    The only writer of ``decisions.state``. Reads the current state under
    ``FOR UPDATE`` so two reviewers acting at once cannot both win.
    """
    if to_state not in LEGAL_TRANSITIONS:
        raise IllegalTransition(f"'{to_state}' is not a decision state")

    row = (
        session.execute(
            text(
                "SELECT state, classification, domain_id FROM decisions "
                "WHERE id = CAST(:d AS uuid) AND tenant_id = CAST(:t AS uuid) FOR UPDATE"
            ),
            {"d": decision_id, "t": ctx.tenant_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        # Not found rather than forbidden: RLS has already scoped this to the
        # caller's tenant, and disclosing that a decision exists elsewhere is
        # itself a disclosure.
        raise NotFound(f"decision {decision_id} was not found")

    from_state: DecisionState = row["state"]
    if not is_legal(from_state, to_state):
        allowed = sorted(LEGAL_TRANSITIONS[from_state])
        raise IllegalTransition(
            f"a decision in {from_state} cannot move to {to_state}; "
            f"legal moves are {allowed or 'none — the case is closed'}"
        )

    _require(ctx, TRANSITION_PERMISSION.get(to_state, "decisions:analyse"), to_state)

    updated = affected_rows(
        session.execute(
            text(
                """
                UPDATE decisions
                   SET state = CAST(:to AS decision_state),
                       updated_at = now(),
                       closed_at = CASE WHEN :to = 'CLOSED' THEN now() ELSE closed_at END
                 WHERE id = CAST(:d AS uuid)
                   AND tenant_id = CAST(:t AS uuid)
                   AND state = CAST(:from AS decision_state)
                """
            ),
            {"to": to_state, "from": from_state, "d": decision_id, "t": ctx.tenant_id},
        )
    )
    if updated != 1:
        # The row moved between the SELECT and the UPDATE despite FOR UPDATE,
        # which should be impossible; refusing is the only safe response.
        raise IllegalTransition("the decision changed state concurrently; retry")

    session.execute(
        text(
            """
            INSERT INTO decision_transitions
                (tenant_id, decision_id, from_state, to_state, actor_user_id, actor_kind, reason)
            VALUES (CAST(:t AS uuid), CAST(:d AS uuid), CAST(:from AS decision_state),
                    CAST(:to AS decision_state), CAST(NULLIF(:actor, '') AS uuid), :kind, :reason)
            """
        ),
        {
            "t": ctx.tenant_id,
            "d": decision_id,
            "from": from_state,
            "to": to_state,
            "actor": (ctx.human.user_id if ctx.human else ""),
            "kind": ctx.actor_type if ctx.actor_type in {"HUMAN", "AGENT"} else "SYSTEM",
            "reason": reason,
        },
    )

    notified = _notify(session, ctx, decision_id=decision_id, to_state=to_state)

    audit(
        session,
        ctx,
        AuditEntry(
            category="APPROVAL" if to_state in {"APPROVED", "REJECTED"} else "USER_ACTION",
            action=f"decision.{to_state.lower()}",
            resource_type="decision",
            resource_id=decision_id,
            classification=str(row["classification"]),
            payload={"from": from_state, "to": to_state, "reason": reason, "notified": notified},
        ),
    )
    return TransitionResult(
        decision_id=decision_id, from_state=from_state, to_state=to_state, notified=notified
    )


def _notify(session: Session, ctx: ExecutionContext, *, decision_id: str, to_state: DecisionState) -> int:
    """Tell the people who can act next, and only them.

    Recipients are derived from two facts at once: holding the permission the
    next step needs, and belonging to the decision's domain. Notifying every
    permission holder across the tenant would leak the existence of decisions
    in domains the recipient cannot open.
    """
    spec = NOTIFY_ON.get(to_state)
    if spec is None:
        return 0
    kind, permission = spec

    return affected_rows(
        session.execute(
            text(
                """
                INSERT INTO notifications
                    (tenant_id, recipient_user_id, decision_id, kind, subject, body)
                SELECT d.tenant_id, tm.user_id, d.id, :kind,
                       d.reference || ' — ' || d.title,
                       'This decision is now ' || d.state || ' and is waiting on you.'
                  FROM decisions d
                  JOIN team_members tm
                    ON tm.tenant_id = d.tenant_id AND tm.domain_id = d.domain_id
                 WHERE d.id = CAST(:d AS uuid)
                   AND d.tenant_id = CAST(:t AS uuid)
                   AND EXISTS (
                         SELECT 1
                           FROM user_roles ur
                           JOIN role_permissions rp ON rp.role_id = ur.role_id
                          WHERE ur.user_id = tm.user_id
                            AND ur.tenant_id = d.tenant_id
                            AND rp.permission_id = :perm
                       )
                """
            ),
            {"kind": kind, "d": decision_id, "t": ctx.tenant_id, "perm": permission},
        )
    )
