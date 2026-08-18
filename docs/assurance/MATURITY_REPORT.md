# Maturity Report — Agentic OS Enterprise v3.1

**Score: 94.55 / 100. Not certified.**

This number was not chosen. It is what the evidence engine computed from a test
run that actually executed, and it is reproducible:

```
agentic-migrate && agentic-seed
agentic-evidence collect --environment development
```

The engine runs the suite, parses the JUnit report, derives each control's
status from whether its named test passed, and computes

```
score = verified applicable weight / total applicable weight * 100
      = 104 / 110 * 100
      = 94.55
```

No status can be entered by hand. A control with no `test:` reference is
reported NOT_EVIDENCED and contributes zero however well it is implemented.

| | |
|---|---|
| Controls assessed | 58 |
| Applicable weight | 110 |
| Verified weight | 104 |
| Critical blockers | none |
| Certified | **no** — certification requires 100 with no critical blocker |
| Test run | 313 tests, 0 failures, 0 errors, 0 skipped |
| Environment | development, against PostgreSQL 16 with pgvector |
| Accessibility | 23 surfaces × 2 colour schemes, 0 violations, 0 serious or critical |

## Why it is not 100

Six weight is unevidenced, in four controls. None of them is a bug and none is
hidden:

| Control | Weight | Status | Why |
|---|---:|---|---|
| DEP-003 | 2 | NOT_EVIDENCED | The platform has never been applied to a Kubernetes cluster. CI now builds and scans the image, renders all four kustomize overlays and runs `terraform fmt`, `init` and `validate` — but `validate` checks coherence, not reality, and no apply has been performed anywhere. |
| IND-001 | 2 | NOT_EVIDENCED | No independent security assessment has been performed. The repository contains a red-team suite, but a suite written by the same author as the control it tests is not independent assurance and is not reported as such. |
| IND-002 | 1 | NOT_EVIDENCED | No control is PRODUCTION_PROVEN. Every passing control is VERIFIED by an automated test in a development or CI environment. The engine keeps the two statuses distinct precisely so this distinction survives. |
| PRF-003 | 1 | NOT_EVIDENCED | Capacity has not been established on representative hardware. The platform is now measured under concurrency — see below — but on one shared development host with a seeded corpus, which characterises saturation without establishing capacity. |

These four controls were **added** to the catalogue during this build. Before
they existed the same test run scored 100.00 / 100 and the engine certified it.
That number was arithmetically correct and substantively misleading: it measured
only the ground the catalogue had chosen to cover. Adding the domains the
platform has not earned is the correction, and the score fell to 95.33 as a
result. The catalogue's total is no longer pinned at 100 for the same reason —
a fixed total means a new unmet control can only be admitted by shrinking an
existing one, which raises the per-control score for doing nothing.

## Domain scores

| Domain | Score | Weight | Verified | Failed | Not evidenced |
|---|---:|---:|---:|---:|---:|
| Agent architecture | 100.00 | 10 | 4 | 0 | 0 |
| Workflow and orchestration | 100.00 | 8 | 4 | 0 | 0 |
| Enterprise architecture | 100.00 | 8 | 3 | 0 | 0 |
| Security | 100.00 | 10 | 7 | 0 | 0 |
| AI governance | 100.00 | 7 | 4 | 0 | 0 |
| Business architecture | 100.00 | 7 | 3 | 0 | 0 |
| Data architecture | 100.00 | 7 | 3 | 0 | 0 |
| RAG and knowledge | 100.00 | 7 | 3 | 0 | 0 |
| UX and accessibility | 100.00 | 7 | 3 | 0 | 0 |
| Reliability | 100.00 | 6 | 3 | 0 | 0 |
| Observability | 100.00 | 5 | 3 | 0 | 0 |
| Evaluation and assurance | 100.00 | 5 | 3 | 0 | 0 |
| Privacy | 100.00 | 5 | 3 | 0 | 0 |
| DevSecOps | 100.00 | 4 | 2 | 0 | 0 |
| **Deployment** | **50.00** | 4 | 2 | 0 | 1 |
| **Performance** | **66.67** | 3 | 2 | 0 | 1 |
| DR and resilience | 100.00 | 2 | 1 | 0 | 0 |
| Business value | 100.00 | 2 | 1 | 0 | 0 |
| **Independent assurance** | **0.00** | 3 | 0 | 0 | 2 |

The full control-by-control table, with the test that evidences each one, is
regenerated into `artifacts/MATURITY_REPORT.md` on every collection, alongside
a hash-addressed `artifacts/evidence-bundle.json`.

## What "verified" means here, precisely

Using the vocabulary the build brief asks for — mapped / tested / verified /
not verified:

* **Verified (54 controls, 104 weight).** An automated test names the control,
  the test executed in this run, and it passed. The tests exercise a real
  PostgreSQL 16 instance with row level security enforced, a real HTTP surface,
  and a real browser for the accessibility pass. Nothing is mocked at the
  boundary the control is about.
* **Not verified (4 controls, 6 weight).** Listed above.
* **Not claimed at all.** No control asserts conformance with an external
  standard. The security architecture maps to recognised control families for
  navigation, but this platform has not been assessed against ISO 27001,
  SOC 2, NIST AI RMF, the EU AI Act or any other scheme by anybody, and no
  document in this repository should be read as saying otherwise.

## Measured, not declared

The platform is now measured under concurrency rather than carrying a declared
latency target. A sweep at concurrency 1, 8 and 32 against the running API,
120 requests per scenario per level, 100{'h1': 5.15, 'h8': 26.27, 'h32': 132.96, 'r1': 12.31, 'r8': 82.04, 'r32': 355.08, 'k1': 13.56, 'k8': 85.18, 'k32': 367.79, 'c1': 17.32, 'c8': 128.4, 'c32': 495.93, 't1': 75.2, 't8': 91.7, 't32': 79.4}uccess at every level:

| Scenario | p50 @1 | p50 @8 | p50 @32 |
|---|---:|---:|---:|
| `/health` | 5.2 ms | 26.3 ms | 133.0 ms |
| runs list (authorization + RLS read) | 12.3 ms | 82.0 ms | 355.1 ms |
| knowledge search (hybrid retrieval, ACL in SQL) | 13.6 ms | 85.2 ms | 367.8 ms |
| command centre (widest read) | 17.3 ms | 128.4 ms | 495.9 ms |

Throughput: 75.2 req/s at concurrency 1, **91.7 at 8**, 79.4 at 32.

The useful finding is the shape, not any single number: **throughput peaks near
concurrency 8 and falls at 32 while latency roughly quadruples.** Past that
point the platform queues rather than doing more work. Nothing failed at any
level — no pool exhaustion, no lost tenant binding — but a deployment sizing
itself from the concurrency-1 numbers would be sizing from the wrong end of the
curve.

This is a saturation characterisation on one shared host, not a capacity
statement. PRF-003 stays NOT_EVIDENCED for exactly that reason.

## What this score does not tell you

* **It is a development-environment result.** Correct behaviour under seeded
  data and a single-node database is not the same as correct behaviour under
  production load, adversarial users and real integrations. See
  `FINAL_GAP_AUDIT.md` for the full list.
* **The catalogue is self-authored.** 55 controls chosen by the builder,
  weighted by the builder. That is a reasonable engineering instrument and a
  weak assurance instrument. It is why IND-001 exists and why it is
  unevidenced.
* **Deterministic providers are the default.** The AI plane runs against
  deterministic local providers unless an external one is configured, so the
  model-facing controls verify the *governance* of model calls — routing,
  classification ceilings, prompt-registry pinning, context-firewall trust
  tiers — rather than the quality of any particular vendor model.

## Recorded evidence

Every collection writes to the database as well as to disk: `evidence_records`
per control per tenant, a `maturity_snapshots` row, and an `audit_events` entry
in the hash-chained ledger. `agentic-evidence report` prints the latest
recorded state; the Evidence surface in the console renders the same rows. The
score in this document, the score in the API and the score on the screen all
come from one computation in `agentic_os.assurance.evidence`.
