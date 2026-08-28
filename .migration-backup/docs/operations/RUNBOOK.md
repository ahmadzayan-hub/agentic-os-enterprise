# Operations Runbook

## Health

| Endpoint | Meaning |
|---|---|
| `GET /health` | Process is up; reports database reachability and version |
| `GET /ready` | Additionally requires pgvector — retrieval is not servable without it |

`/ready` returning 503 with `pgvector: false` means the extension is missing
from the target database, not that the platform is broken. Fix it with the
cluster bootstrap, not with a restart.

## Deploying

1. `agentic-migrate` runs as a Job, once per release — not as an init container
   on the API, so a failed migration stops the rollout instead of crash-looping
   every replica.
2. API, worker and web roll with `maxUnavailable: 0`.
3. Verify `/ready`, then run `agentic-evidence collect --environment <env>`.

Rollback: redeploy the previous image tag. Migrations are forward-only — a
schema change that must be undone needs a new migration, never an edit to an
applied one (the checksum check will reject it).

## Common situations

### Runs are queued and not progressing
The worker advances runs; the API only starts them. Check that at least one
worker pod is running and look at its last pass:

```bash
agentic-worker --once      # a single pass, prints what it did
```

A pass reports `runs_advanced`, `events_dispatched`, `approvals_expired` and
any per-tenant errors. An error against one tenant does not stop the others.

If a run is `RUNNING` with a stale `lease_expires_at`, its worker died; the
lease expires and another worker resumes from the last committed step. Nothing
needs to be done by hand.

### A run is stuck at an approval
That is not stuck, it is parked. A paused run consumes no worker. It resumes
when an approver decides, or it expires and the worker sweeps it.

### The outbox is backing up
`outbox_events` with status `PENDING` and `next_attempt_at` in the past means
handlers are failing or no worker is running. Entries retry with exponential
backoff and move to `DEAD` after `max_attempts`; dead letters need a human.
Look at `last_error` before replaying anything.

### Costs are climbing
Check the Cost surface, then the budgets. A tenant budget with `hard_stop` set
stops runs when exhausted; a soft budget routes to the fallback model and
records the substitution. If neither is happening, the budget row is missing —
`agentic-seed` re-creates the defaults.

### Everything must stop now
Engage a kill switch (see `docs/security/SECURITY_OPERATIONS.md`). It takes
effect at the next gateway decision. Do not scale to zero: that loses the
ability to investigate and leaves leases hanging.

## Backup and restore

Weekly by CronJob, or on demand:

```bash
agentic-dr run --environment production
agentic-dr latest
```

Outcomes: `SUCCESS` (row counts matched and every tenant's audit chain
re-hashed intact in the restored copy), `PARTIAL` (restored but something did
not verify — read `notes`), `FAILURE` (the restore did not complete).

Treat `PARTIAL` as seriously as `FAILURE`. A copy that restores but whose
ledger does not verify is not a recoverable system.

If the exercise reports `NOT_RUN`, `AGENTIC_DR_ADMIN_URL` is not configured.
That is the designed behaviour — the exercise refuses to record evidence for
something it did not do.

**Real restore, not a drill.** Restore the dump into the target database with
`pg_restore`, then run `agentic-migrate` (a no-op if the dump is current) and
verify the chain for every tenant before letting traffic in. A restored copy
with a broken chain must be investigated before it serves anyone.

## Evidence and certification

`agentic-evidence collect` runs the suite, derives each control's status from
the JUnit report, computes maturity and writes `evidence_records`, a
`maturity_snapshots` row and a ledger entry. It cannot be told what the score
is.

Certification requires 100 with no critical blocker. A critical control that is
not VERIFIED blocks certification whatever the numerical score — if the run
scores 99 with a failed critical control, it is refused, and that is the
intended behaviour, not a bug to work around.

## Observability

Spans and metrics are written to the database (`trace_spans`,
`metric_samples`) and surfaced through the Analytics surface and
`GET /api/v1/analytics`. Every span carries the correlation id, which is also
on every ledger entry — one identifier ties a user request to its plan, its
tool calls, its model calls and its audit trail.

The worker writes a `worker.pass` metric every pass. Absence of that metric is
the cleanest signal that the worker has stopped.
