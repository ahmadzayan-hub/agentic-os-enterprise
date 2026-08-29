# Tenant Offboarding

**Status:** implemented and tested; **never executed against a real tenant.**
Treat the timings and side effects below as what the code does, not as
operational experience.

---

## Why a tenant cannot be deleted

`audit_events` and `decision_transitions` are append-only under triggers that
fire on cascaded deletes as well as direct ones. `DELETE FROM tenants` therefore
fails the moment the tenant has any history — which every real tenant does
within minutes of being provisioned.

That is the guarantee, not a defect. A ledger that vanishes when its tenant does
is not a ledger, and a decision whose history can be erased is not a record. The
moment those tables would be most convenient to lose is exactly the moment a
relationship ends badly.

So offboarding **retires** a tenant. It does not erase one.

## What retirement does

`agentic_os.privacy.offboarding.retire_tenant`, in order:

| Step | Effect | Refuses if |
|---|---|---|
| 1 | Checks for an active legal hold | — |
| 2 | Revokes every session | no `org:write`, no MFA, or no stated reason |
| 3 | Pseudonymises every user's email, display name and attributes | — |
| 4 | Marks the tenant `RETIRED` and stamps `deleted_at` | already retired |
| 5 | Writes `tenant.retired` to the audit ledger | — |

A tenant can only be retired from a session **bound to that tenant**. Retiring
another tenant from this one's session is a cross-tenant write, which the whole
isolation model exists to prevent, so it is refused rather than special-cased.

## What it deliberately leaves behind

The operation returns this list to the caller, so an operator learns it now
rather than during an audit two years later:

| Retained | Why |
|---|---|
| `audit_events` | The hash-chained ledger is the evidence of correct behaviour while the tenant was served |
| `decision_transitions` | Append-only under the same triggers; a decision whose history can be erased is not a record |
| `decisions` | Retained because their history is — an orphaned history is worse than a retained decision |
| `backup_records` | Backup provenance outlives the tenant by design |

Pseudonymisation means those records still exist but no longer identify anyone:
`users.email` becomes `retired+<prefix>@invalid`, which is deliberately not a
routable address.

## A legal hold outranks retirement

If any `legal_holds` row is active, the operation returns `BLOCKED_BY_HOLD`,
changes nothing, and records the refusal in the ledger. A hold is a legal
instruction; retirement is a commercial decision. Release the hold first, or
do not retire.

## The purge that this is not

Removing the retained records is a **separate** action on the tenant's stated
clock (`tenants.retention_days`, default 730). It is not implemented here and is
not implied by retirement.

`NOT_IMPLEMENTED` — a purge would have to disable the append-only triggers,
which requires the table owner and a deliberate act. That is correct: the
procedure for destroying a ledger should be inconvenient and should require
somebody to decide to do it. When it is built it will need its own
authorisation, its own evidence, and a record written somewhere the purge
cannot reach.

## Verifying a retirement

```sql
-- 1. The tenant is retired and nothing can bind to it for new work.
SELECT status, deleted_at FROM tenants WHERE id = :tenant;

-- 2. No session survives.
SELECT count(*) FROM sessions WHERE tenant_id = :tenant AND revoked_at IS NULL;

-- 3. No user is identifiable.
SELECT count(*) FROM users WHERE tenant_id = :tenant AND email NOT LIKE 'retired+%';

-- 4. The ledger is intact and records the retirement.
SELECT * FROM audit_verify_chain(:tenant);
SELECT action, occurred_at FROM audit_events
 WHERE tenant_id = :tenant ORDER BY sequence_no DESC LIMIT 1;
```

Steps 1–3 should read `RETIRED`, `0` and `0`. Step 4 should report an intact
chain whose most recent entry is `tenant.retired`.

## Known gaps

- `NOT_VERIFIED` — never run against a tenant with production-scale history.
  The tests exercise a scratch tenant with a handful of rows.
- `NOT_IMPLEMENTED` — no retention purge (above).
- `NOT_IMPLEMENTED` — no export of the tenant's data before retirement. A
  customer entitled to take their records with them currently has to be served
  by a DSAR access request per subject.
