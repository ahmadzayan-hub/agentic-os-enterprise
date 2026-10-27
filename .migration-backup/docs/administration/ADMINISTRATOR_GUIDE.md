# Administrator Guide

For the people who provision tenants, manage identities, set budgets and keep
the platform running.

## Provisioning

**A tenant.** Tenants are created through `platform_provision_tenant()`, a
SECURITY DEFINER function reachable only through the provisioning role. The
application role cannot create one: row level security has no bypass predicate,
so a tenant that does not exist yet cannot be written by a session bound to it.
`agentic-seed` uses the same path.

**A user.** Users are created inside a tenant with a clearance
(`PUBLIC` → `INTERNAL` → `CONFIDENTIAL` → `RESTRICTED`) and one or more roles.
Clearance and role are independent: a role grants *permissions*, clearance
governs *which classifications of data those permissions may touch*. A
principal needs both to see a RESTRICTED document.

**Roles.** The shipped roles are `operator`, `approver`, `analyst`, `builder`,
`auditor`, `security_admin`, `governance_admin`, `executive` and
`platform_admin`. Permissions are listed in
`packages/agentic_os/src/agentic_os/identity/permissions.py`. Two permissions —
`audit:read` and `privacy:read` — are deliberately excluded from the blanket
read-only grant and must be granted explicitly, because they expose who did
what and whose personal data is held.

**Multi-factor.** `platform_admin`, `security_admin` and `auditor` require a
second factor; a member of one of those roles who is not enrolled cannot sign
in. Enrolment issues a TOTP secret sealed in a KMS envelope bound to the user
id, so a ciphertext lifted from the table cannot be replayed against another
user. A code accepted for one time step is never accepted twice.

## Autonomy

Five levels govern how much an agent may do without a person:

| Level | Meaning |
|---|---|
| A0 | Suggest only; no execution |
| A1 | Read-only execution |
| A2 | Reversible writes in non-production scope |
| A3 | Reversible writes under an explicit policy rule, with a verification obligation |
| A4 | Consequential or irreversible action — **always requires human approval** |

An agent's contract sets a ceiling. A request above that ceiling is refused by
the validator before anything runs, not by the tool at the end.

## Budgets and kill switches

Budgets are per tenant and per run, in both currency and tokens, with a
`hard_stop` flag and a fallback model. When a hard-stop budget is exhausted the
run stops; when it is soft, the gateway routes to the fallback model and
records the substitution.

Kill switches exist at tenant, agent, tool and read-only scope. Engaging the
`READ_ONLY` switch leaves the platform answerable but refuses every write.
Engaging a tenant switch stops that tenant entirely. Both take effect on the
next gateway decision — no restart, no deploy.

## Approvals

An approval is raised by the control plane, not by the agent. It carries the
action, the target, the risk class, the financial impact, the reversibility,
the reason, the consequences, the evidence and the policy references that
produced it. Approvers see it on the Approvals surface; a decision is recorded
in the ledger with the approver's identity. Approvals expire; expired ones are
swept by the worker and never silently succeed.

## The console

Navigation is filtered by the signed-in principal's permissions, so what an
administrator sees differs from what an operator sees. This is presentation
only — the API authorises every request independently, and a hidden route typed
into the address bar is still refused.

The **Capabilities** surface is the honest inventory: every declared capability
with its implementation status. A tool marked NOT_IMPLEMENTED appears there and
is refused at the gateway; it never appears as a working control.

## Routine operations

| Task | Command |
|---|---|
| Apply migrations | `agentic-migrate` |
| Seed or re-sync registries | `agentic-seed` |
| Run the worker | `agentic-worker` (or `--once` for a single pass) |
| Collect evidence and derive maturity | `agentic-evidence collect --environment <env>` |
| Show the recorded maturity | `agentic-evidence report` |
| Run a restore exercise | `agentic-dr run --environment <env>` |
| Show the last restore exercise | `agentic-dr latest` |

The worker is safe to scale horizontally: workflow runs are leased and outbox
rows are taken with `FOR UPDATE SKIP LOCKED`, so two workers never take the
same work. On `SIGTERM` a worker finishes the pass in flight rather than
abandoning a leased run — allow at least 60 seconds of termination grace.

## Data subject requests

Raise a request on the Privacy surface or through
`POST /api/v1/privacy/requests`, then process it. Two behaviours matter:

* **A legal hold beats a deletion request.** A request that would remove data
  under an active hold is parked as `BLOCKED_BY_HOLD` with the hold named. It
  is never partially executed.
* **The audit ledger is never deleted.** It is append-only by construction, so
  erasure pseudonymises the subject's identifiers instead. The trade-off is
  recorded on the request so a reviewer sees it.

Erasure detaches the subject from operational records, deletes credential
material outright and anonymises the account. Exports exclude password hashes,
session tokens and MFA material — a subject access request must not become a
credential disclosure channel.

## Backup and restore

`agentic-dr run` takes a real `pg_dump`, restores it into a scratch database,
compares every table's row count against the source, recomputes each tenant's
audit hash chain inside the restored copy, records the measured RPO and RTO,
and drops the scratch database. A copy that restores but whose ledger does not
verify is recorded as `PARTIAL` — restoring bytes is not recovering a
trustworthy system.

Two things to know:

* It needs `AGENTIC_DR_ADMIN_URL`, a maintenance identity that can create a
  database and install extensions. Without it the exercise refuses to run and
  control DRP-001 stays NOT_EVIDENCED. That is deliberate: no configuration
  must mean no evidence, never fabricated evidence.
* Because every table has FORCE row level security with no bypass predicate,
  the migration owner's own `COPY` is filtered and `pg_dump` fails as the
  owner. A complete backup is a privileged operation by construction. This is
  the isolation model working as intended, not a defect.

## What is not implemented

Read `docs/assurance/FINAL_GAP_AUDIT.md` before planning a deployment. The
headline items: no OIDC or SAML federation, rate limiting is per-instance, the
`vault` and cloud KMS backends have never run against the real service, and
the platform has never been deployed to a cluster.
