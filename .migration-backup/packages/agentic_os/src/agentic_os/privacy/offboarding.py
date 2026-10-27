"""Retiring a tenant.

The readiness report recorded this as a risk rather than a feature, and it
still is one until somebody runs it against a real tenant. What follows is the
procedure, expressed as code so it cannot drift from the runbook that describes
it.

Why a tenant cannot simply be deleted
-------------------------------------
``audit_events`` and ``decision_transitions`` are append-only under triggers
that fire on cascaded deletes too. So ``DELETE FROM tenants`` fails the moment
the tenant has any history — which every real tenant does. That is not an
oversight to work around; it is the guarantee those tables exist to provide. A
ledger that disappears when its tenant does is not a ledger.

So offboarding **retires** rather than erases:

1. Refuse outright while a legal hold is active. A hold outranks a commercial
   decision to stop serving someone.
2. Revoke every session, so access stops immediately rather than when tokens
   happen to expire.
3. Pseudonymise the identifying fields on each user, using the same treatment
   subject erasure already applies.
4. Mark the tenant ``RETIRED`` and stamp ``deleted_at``. Row level security
   still confines the remaining rows to that tenant, and nothing can bind to it
   for new work.
5. Record the whole thing in the ledger, which is the one place that keeps its
   memory of what happened.

What is deliberately *not* done
-------------------------------
The ledger and the decision history stay. They are the evidence that the
platform behaved correctly while the tenant was served, and destroying them at
the moment a relationship ends is exactly when their absence would be most
convenient. Their eventual removal is a retention decision on a stated clock —
``tenants.retention_days`` — carried out by a separate, deliberate purge that a
person authorises with the owner role. This module does not do it, and says so
rather than leaving a caller to assume it did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.assurance.audit import AuditEntry, AuditLedger
from agentic_os.core.context import ExecutionContext
from agentic_os.core.db import affected_rows
from agentic_os.core.errors import AuthorizationError, Conflict, NotFound

#: What retirement leaves behind, and why. Reported to the caller so an
#: operator closing a tenant is told what still exists rather than discovering
#: it during an audit two years later.
RETAINED: dict[str, str] = {
    "audit_events": (
        "the hash-chained ledger is append-only and is the evidence of correct "
        "behaviour while the tenant was served"
    ),
    "decision_transitions": (
        "a decision's state history is append-only under the same triggers; a "
        "decision whose history can be erased is not a record"
    ),
    "decisions": (
        "retained because their transition history is, and an orphaned history "
        "is worse than a retained decision"
    ),
    "backup_records": "backup provenance outlives the tenant by design",
}


@dataclass(slots=True)
class OffboardingResult:
    tenant_id: str
    status: str
    sessions_revoked: int = 0
    users_pseudonymised: int = 0
    blocked_by: list[str] = field(default_factory=list)
    retained: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "status": self.status,
            "sessions_revoked": self.sessions_revoked,
            "users_pseudonymised": self.users_pseudonymised,
            "blocked_by": self.blocked_by,
            "retained": self.retained,
            "note": (
                "The tenant is retired, not erased. The records listed under "
                "'retained' remain and are removed only by a separate retention "
                "purge on the tenant's stated clock."
            ),
        }


def retire_tenant(
    session: Session, ctx: ExecutionContext, *, tenant_id: str, reason: str
) -> OffboardingResult:
    """Retire a tenant, or refuse and say why.

    Runs against the *bound* tenant only. Retiring some other tenant from a
    session bound to this one would be a cross-tenant write, which is the one
    thing this platform's whole isolation model exists to prevent — so it is
    refused rather than special-cased.
    """
    human = ctx.human
    if human is None:
        raise AuthorizationError("retiring a tenant requires a human principal")
    granted = human.permissions
    if "*" not in granted and "org:write" not in granted:
        raise AuthorizationError("permission 'org:write' is required to retire a tenant")
    if not human.mfa_satisfied:
        raise AuthorizationError("retiring a tenant requires a second factor")
    if not reason.strip():
        raise Conflict("a retirement must carry a stated reason")

    if tenant_id != ctx.tenant_id:
        raise AuthorizationError(
            "a tenant can only be retired from a session bound to it; cross-tenant retirement is refused"
        )

    row = (
        session.execute(
            text("SELECT id, slug, status FROM tenants WHERE id = CAST(:t AS uuid)"),
            {"t": tenant_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise NotFound(f"tenant {tenant_id} was not found")
    if str(row["status"]) == "RETIRED":
        raise Conflict("this tenant is already retired")

    holds = [
        str(h)
        for h in session.execute(
            text(
                "SELECT hold_key FROM legal_holds WHERE tenant_id = CAST(:t AS uuid) "
                "AND active AND released_at IS NULL"
            ),
            {"t": tenant_id},
        ).scalars()
    ]
    if holds:
        # A hold is a legal instruction. It outranks a commercial decision to
        # stop serving someone, so this refuses rather than proceeding and
        # noting the conflict afterwards.
        AuditLedger(session).append(
            ctx,
            AuditEntry(
                category="CONFIG_CHANGE",
                action="tenant.retirement_blocked_by_legal_hold",
                outcome="DENIED",
                resource_type="tenant",
                resource_id=tenant_id,
                classification="RESTRICTED",
                payload={"holds": holds, "reason": reason},
            ),
        )
        return OffboardingResult(tenant_id=tenant_id, status="BLOCKED_BY_HOLD", blocked_by=holds)

    revoked = affected_rows(
        session.execute(
            text(
                "UPDATE sessions SET revoked_at = now() "
                "WHERE tenant_id = CAST(:t AS uuid) AND revoked_at IS NULL"
            ),
            {"t": tenant_id},
        )
    )

    # The same pseudonymisation subject erasure applies, so a retired tenant's
    # users are no more identifiable than an erased subject.
    pseudonymised = affected_rows(
        session.execute(
            text(
                """
                UPDATE users
                   SET email = 'retired+' || left(id::text, 8) || '@invalid',
                       display_name = 'Retired user',
                       attributes = '{}'::jsonb,
                       deleted_at = COALESCE(deleted_at, now())
                 WHERE tenant_id = CAST(:t AS uuid)
                   AND email NOT LIKE 'retired+%'
                """
            ),
            {"t": tenant_id},
        )
    )

    session.execute(
        text(
            "UPDATE tenants SET status = 'RETIRED', deleted_at = COALESCE(deleted_at, now()), "
            "updated_at = now() WHERE id = CAST(:t AS uuid)"
        ),
        {"t": tenant_id},
    )

    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="CONFIG_CHANGE",
            action="tenant.retired",
            resource_type="tenant",
            resource_id=tenant_id,
            classification="RESTRICTED",
            payload={
                "slug": str(row["slug"]),
                "reason": reason,
                "sessions_revoked": revoked,
                "users_pseudonymised": pseudonymised,
                "retained": sorted(RETAINED),
            },
        ),
    )

    return OffboardingResult(
        tenant_id=tenant_id,
        status="RETIRED",
        sessions_revoked=revoked,
        users_pseudonymised=pseudonymised,
        retained=dict(RETAINED),
    )
