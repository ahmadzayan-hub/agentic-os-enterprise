"""Human Approval Engine.

An approval request is the platform's record that a machine proposed something
consequential and a named human decided about it. The card carries everything a
reviewer needs to decide without leaving the screen: proposing agent, action,
target, financial impact, confidence, reasoning, evidence, sources, risk,
reversibility, governing policy, consequences and autonomy level.

Supported modes: SINGLE, DUAL (two distinct approvers), SEQUENTIAL (ordered
stages) and PARALLEL (any N of the nominated approvers). Delegation, expiry,
escalation, rejection and request-changes are all first-class outcomes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.assurance.audit import AuditEntry, AuditLedger
from agentic_os.core.context import ExecutionContext
from agentic_os.core.db import affected_rows
from agentic_os.core.errors import Conflict, NotFound, ValidationError
from agentic_os.core.ids import utcnow

ApprovalMode = Literal["SINGLE", "DUAL", "SEQUENTIAL", "PARALLEL"]
Decision = Literal["APPROVED", "REJECTED", "CHANGES_REQUESTED"]

DEFAULT_TTL_HOURS = 24


@dataclass(slots=True)
class ApprovalCard:
    """Everything a human needs to decide, in one payload."""

    action: str
    target: str = ""
    proposing_agent: str = ""
    autonomy_level: str = "A4"
    risk_class: str = "HIGH"
    financial_impact_usd: float = 0.0
    reversibility: str = "IRREVERSIBLE"
    confidence: float | None = None
    reason: str = ""
    consequences: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    policy_refs: list[dict[str, Any]] = field(default_factory=list)

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("action", self.action),
                ("reason", self.reason),
                ("consequences", self.consequences),
            )
            if not str(value).strip()
        ]
        if missing:
            raise ValidationError(
                "an approval card must explain what is proposed and what follows from it",
                details={"missing": missing},
            )


def request_approval(
    session: Session,
    ctx: ExecutionContext,
    card: ApprovalCard,
    *,
    mode: ApprovalMode = "SINGLE",
    required_approvals: int = 1,
    approver_roles: list[str] | None = None,
    approver_user_ids: list[str] | None = None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    escalate_to_role: str = "",
    approve_and_execute: bool = False,
    run_id: str | None = None,
    run_step_id: str | None = None,
) -> str:
    """Create an approval request and its approver slots. Returns the id."""
    card.validate()
    if mode == "DUAL":
        required_approvals = max(2, required_approvals)
    if required_approvals < 1:
        raise ValidationError("required_approvals must be at least 1")

    approver_roles = approver_roles or ["approver"]
    approver_user_ids = approver_user_ids or []

    row = session.execute(
        text(
            """
            INSERT INTO approvals (
              tenant_id, run_id, run_step_id, requested_by_agent, mode, required_approvals,
              action, target, autonomy_level, risk_class, financial_impact_usd,
              reversibility, confidence, reason, evidence, sources, consequences,
              policy_refs, approve_and_execute, expires_at, escalate_to_role)
            VALUES (
              :t, :run, :step, :agent, :mode, :req, :action, :target,
              CAST(:aut AS autonomy_level), CAST(:risk AS risk_class), :fin,
              :rev, :conf, :reason, CAST(:evidence AS jsonb), CAST(:sources AS jsonb),
              :consequences, CAST(:policies AS jsonb), :aae, :expires, :escalate)
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id,
            "run": run_id or (ctx.run_id or None),
            "step": run_step_id,
            "agent": card.proposing_agent or (ctx.agent.agent_id if ctx.agent else ""),
            "mode": mode,
            "req": required_approvals,
            "action": card.action,
            "target": card.target,
            "aut": card.autonomy_level,
            "risk": card.risk_class,
            "fin": card.financial_impact_usd,
            "rev": card.reversibility,
            "conf": card.confidence,
            "reason": card.reason,
            "evidence": json.dumps(card.evidence, default=str),
            "sources": json.dumps(card.sources, default=str),
            "consequences": card.consequences,
            "policies": json.dumps(card.policy_refs, default=str),
            "aae": approve_and_execute,
            "expires": utcnow() + timedelta(hours=ttl_hours),
            "escalate": escalate_to_role,
        },
    ).one()
    approval_id = str(row.id)

    sequence = 1
    for user_id in approver_user_ids:
        session.execute(
            text(
                "INSERT INTO approval_steps (tenant_id, approval_id, sequence, approver_user_id) "
                "VALUES (:t, :a, :s, :u)"
            ),
            {"t": ctx.tenant_id, "a": approval_id, "s": sequence, "u": user_id},
        )
        if mode == "SEQUENTIAL":
            sequence += 1
    for role in approver_roles:
        session.execute(
            text(
                "INSERT INTO approval_steps (tenant_id, approval_id, sequence, approver_role) "
                "VALUES (:t, :a, :s, :r)"
            ),
            {"t": ctx.tenant_id, "a": approval_id, "s": sequence, "r": role},
        )
        if mode == "SEQUENTIAL":
            sequence += 1

    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="APPROVAL",
            action="approval.requested",
            resource_type="approval",
            resource_id=approval_id,
            payload={
                "mode": mode,
                "required_approvals": required_approvals,
                "action": card.action,
                "target": card.target,
                "risk_class": card.risk_class,
                "financial_impact_usd": card.financial_impact_usd,
                "autonomy_level": card.autonomy_level,
            },
        ),
    )
    return approval_id


def get_approval(session: Session, tenant_id: str, approval_id: str) -> dict[str, Any]:
    row = (
        session.execute(
            text("SELECT * FROM approvals WHERE tenant_id = :t AND id = :i"),
            {"t": tenant_id, "i": approval_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound(f"approval {approval_id} not found")
    steps = (
        session.execute(
            text(
                "SELECT id, sequence, approver_user_id, approver_role, delegated_from, "
                "decision, comment, decided_at FROM approval_steps "
                "WHERE approval_id = :i ORDER BY sequence, created_at"
            ),
            {"i": approval_id},
        )
        .mappings()
        .all()
    )
    payload = dict(row)
    payload["steps"] = [dict(s) for s in steps]
    return payload


def _expire_if_due(session: Session, approval: dict[str, Any]) -> bool:
    if approval["status"] != "PENDING":
        return False
    if approval["expires_at"] > utcnow():
        return False
    session.execute(
        text("UPDATE approvals SET status = 'EXPIRED', decided_at = now() WHERE id = :i"),
        {"i": approval["id"]},
    )
    return True


def decide(
    session: Session,
    ctx: ExecutionContext,
    approval_id: str,
    decision: Decision,
    *,
    comment: str = "",
    delegated_from: str | None = None,
) -> dict[str, Any]:
    """Record one human decision and resolve the approval if it is complete."""
    if ctx.human is None:
        raise ValidationError("only a human principal may decide an approval")

    approval = get_approval(session, ctx.tenant_id, approval_id)
    if _expire_if_due(session, approval):
        raise Conflict("approval has expired", details={"approval_id": approval_id})
    if approval["status"] != "PENDING":
        raise Conflict(f"approval is already {approval['status']}", details={"approval_id": approval_id})

    user_id = ctx.human.user_id
    already = [
        s
        for s in approval["steps"]
        if s["decision"] != "PENDING" and str(s["approver_user_id"] or "") == user_id
    ]
    if already:
        raise Conflict("this approver has already decided; separation of duties requires distinct approvers")

    # Find a slot this principal may fill: their own named slot, or the
    # lowest-sequence open role slot they hold.
    slot = next(
        (
            s
            for s in approval["steps"]
            if s["decision"] == "PENDING" and str(s["approver_user_id"] or "") == user_id
        ),
        None,
    )
    if slot is None:
        slot = next(
            (
                s
                for s in approval["steps"]
                if s["decision"] == "PENDING" and s["approver_role"] and s["approver_role"] in ctx.human.roles
            ),
            None,
        )
    if slot is None:
        raise ValidationError(
            "principal holds no open approver slot on this request",
            details={"roles": sorted(ctx.human.roles)},
        )

    if approval["mode"] == "SEQUENTIAL":
        open_before = [
            s for s in approval["steps"] if s["decision"] == "PENDING" and s["sequence"] < slot["sequence"]
        ]
        if open_before:
            raise Conflict(
                "a prior approval stage is still outstanding",
                details={"awaiting_sequence": min(s["sequence"] for s in open_before)},
            )

    session.execute(
        text(
            "UPDATE approval_steps SET decision = CAST(:d AS approval_status), comment = :c, "
            "approver_user_id = :u, delegated_from = :df, decided_at = now() WHERE id = :i"
        ),
        {"d": decision, "c": comment, "u": user_id, "df": delegated_from, "i": slot["id"]},
    )

    refreshed = get_approval(session, ctx.tenant_id, approval_id)
    approvals_given = sum(1 for s in refreshed["steps"] if s["decision"] == "APPROVED")
    rejected = any(s["decision"] == "REJECTED" for s in refreshed["steps"])
    changes = any(s["decision"] == "CHANGES_REQUESTED" for s in refreshed["steps"])

    if rejected:
        final = "REJECTED"
    elif changes:
        final = "CHANGES_REQUESTED"
    elif approvals_given >= int(refreshed["required_approvals"]):
        final = "APPROVED"
    else:
        final = "PENDING"

    if final != "PENDING":
        session.execute(
            text(
                "UPDATE approvals SET status = CAST(:s AS approval_status), decided_at = now() WHERE id = :i"
            ),
            {"s": final, "i": approval_id},
        )

    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="APPROVAL",
            action=f"approval.{decision.lower()}",
            outcome="SUCCESS" if decision == "APPROVED" else "DENIED",
            resource_type="approval",
            resource_id=approval_id,
            payload={
                "decision": decision,
                "comment": comment,
                "delegated_from": delegated_from,
                "approvals_given": approvals_given,
                "required": refreshed["required_approvals"],
                "resulting_status": final,
            },
        ),
    )
    return {
        "approval_id": approval_id,
        "status": final,
        "approvals_given": approvals_given,
        "required_approvals": int(refreshed["required_approvals"]),
    }


def delegate(session: Session, ctx: ExecutionContext, approval_id: str, to_user_id: str) -> None:
    """Hand a named approver slot to another user, recording the chain."""
    if ctx.human is None:
        raise ValidationError("only a human principal may delegate an approval")
    approval = get_approval(session, ctx.tenant_id, approval_id)
    slot = next(
        (
            s
            for s in approval["steps"]
            if s["decision"] == "PENDING" and str(s["approver_user_id"] or "") == ctx.human.user_id
        ),
        None,
    )
    if slot is None:
        raise ValidationError("principal holds no open named slot on this approval")
    session.execute(
        text("UPDATE approval_steps SET approver_user_id = :to, delegated_from = :from_ WHERE id = :i"),
        {"to": to_user_id, "from_": ctx.human.user_id, "i": slot["id"]},
    )
    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="APPROVAL",
            action="approval.delegated",
            resource_type="approval",
            resource_id=approval_id,
            payload={"to_user_id": to_user_id},
        ),
    )


def expire_due_approvals(session: Session, tenant_id: str) -> int:
    """Expire every overdue pending approval. Returns the count expired."""
    result = session.execute(
        text(
            "UPDATE approvals SET status = 'EXPIRED', decided_at = now() "
            "WHERE tenant_id = :t AND status = 'PENDING' AND expires_at <= now()"
        ),
        {"t": tenant_id},
    )
    return affected_rows(result)


def pending_for_principal(session: Session, ctx: ExecutionContext, limit: int = 50) -> list[dict]:
    """Approvals this principal can act on right now."""
    if ctx.human is None:
        return []
    rows = session.execute(
        text(
            """
            SELECT DISTINCT a.id, a.action, a.target, a.risk_class, a.autonomy_level,
                   a.financial_impact_usd, a.reversibility, a.confidence, a.reason,
                   a.consequences, a.mode, a.required_approvals, a.requested_by_agent,
                   a.expires_at, a.created_at, a.run_id, a.evidence, a.sources, a.policy_refs
            FROM approvals a
            JOIN approval_steps s ON s.approval_id = a.id
            WHERE a.tenant_id = :t
              AND a.status = 'PENDING'
              AND a.expires_at > now()
              AND s.decision = 'PENDING'
              AND (s.approver_user_id = CAST(:u AS uuid) OR s.approver_role = ANY(:roles))
            ORDER BY a.created_at DESC
            LIMIT :limit
            """
        ),
        {
            "t": ctx.tenant_id,
            "u": ctx.human.user_id,
            "roles": list(ctx.human.roles),
            "limit": limit,
        },
    ).mappings()
    return [dict(r) for r in rows]
