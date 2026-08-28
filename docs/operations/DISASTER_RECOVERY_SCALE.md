# Disaster Recovery at Scale — Measured

**What this closes:** the readiness report's first next action. The DR exercise
passed in about one second on 12,250 rows, which proves the mechanism and says
nothing about scale.

**What this does not close:** production. These figures come from one machine
with synthetic rows. See *Honest limits* below before quoting them.

---

## Method

The **real** exercise — `agentic_os.resilience.backup.run_exercise` — was run
unchanged at four database sizes. It dumps the live database, restores it into a
scratch database, compares every table row-for-row, **re-hashes the entire audit
chain inside the restored copy**, and drops the scratch database afterwards.

Volume was added to the tables that dominate a busy tenant: the audit ledger and
decision evidence. The rows are synthetic and say nothing meaningful; what is
being measured is `pg_dump`/`pg_restore` throughput and the cost of re-hashing
the ledger, neither of which cares whether the text means anything.

## Results

| Ledger rows | Database size | Dump + restore + verify | Entries re-hashed | Outcome |
|---:|---:|---:|---:|---|
| 24,797 | 60 MB | 3.0 s | 24,797 | SUCCESS |
| 99,797 | 97 MB | 5.2 s | 99,797 | SUCCESS |
| 299,797 | 199 MB | 12.6 s | 299,797 | SUCCESS |
| 699,797 | 386 MB | 28.5 s | 699,797 | SUCCESS |

Every run compared every table and found no mismatch, and **every run re-hashed
the complete chain and found it intact** — not a sample.

## Reading the curve

Between 99,797 and 699,797 rows the cost grew from 5.2 s to 28.5 s: 600,000 rows
for 23.3 s, or roughly **26,000 rows per second**, and about **13.5 MB/s** of
database. The relationship is close to linear over this range, which is what one
would expect when the work is dominated by sequential dump and restore rather
than by index rebuilds.

**Extrapolation, clearly labelled as such.** At that rate a 10-million-entry
ledger (~5.5 GB) would take on the order of **6–7 minutes**. That is arithmetic
on four measurements, not a fifth measurement. Real databases have wider tables,
more indexes, live traffic during the drill, and network between the dump and the
restore target — every one of which makes it slower.

## A finding worth recording

The first attempt at this reported `PARTIAL`. The synthetic ledger rows had been
given a `previous_hash` derived from their sequence number rather than continued
from the real chain's tip, so the chain broke at the join — and the exercise
detected it, stopped at the break, and refused to call the restore a success.

That was my error, not the platform's, and it is the most useful result here: the
ledger verification is not a rubber stamp. A restore that produced a
tamper-evident chain with a broken link would have been reported as intact by any
implementation that merely counted rows.

## Honest limits

| Limit | Marker |
|---|---|
| One machine, local disk, no network between dump and restore | `NOT_VERIFIED` at production topology |
| Synthetic rows in two tables; production has wider rows and more indexes | `NOT_VERIFIED` at production shape |
| No concurrent write traffic during the drill | `NOT_VERIFIED` under load |
| Largest measured size 386 MB; production may be orders larger | Extrapolation only beyond this |
| Restore target was the same PostgreSQL instance | `NOT_VERIFIED` cross-host |

The RPO figure the exercise reports stays 0 s throughout, which is a property of
dumping a quiescent database — it is not evidence about RPO under continuous
write traffic, and should not be quoted as such.

## Reproducing

The measurement harness is not committed: it writes hundreds of thousands of
synthetic ledger rows, and a script that does that living in the repository is an
invitation to run it against something that matters. The method is described
above in enough detail to rebuild in a few minutes, and the exercise it calls is
`agentic_os.resilience.backup.run_exercise`, which is committed and tested.
