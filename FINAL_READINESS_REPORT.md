# Final Readiness Report

**Repository:** `ahmadzayan-hub/agentic-os-enterprise`
**Branch:** `claude/agentic-os-enterprise-v3.1-ogi9cq`
**Baseline:** `2148c0e` · **Head:** see git log
**Date:** 2026-08-28

---

## FINAL SCORE: 95.59/100

**Certification is refused**, by the platform's own evidence engine, which certifies only
at a perfect 100 with no critical control unevidenced. There are no critical blockers —
all 70 controls that can be evidenced are — and the 4.41 points missing are exactly three
things the platform has not earned:

| Domain | Score | Why |
|---|---:|---|
| Independent assurance | 0 / 3 | Nobody outside this work has assessed it |
| Deployment | 2 / 4 | Never applied to a cluster |
| Performance | 2 / 3 | One control has no load profile behind it |

Those three domains exist in the catalogue for this purpose. Removing them would raise
the score to 100 without changing anything about the platform, which is precisely the
manoeuvre the catalogue is designed to prevent.

The score is *not* the readiness verdict. §11 is, and every cell of its Production Ready
column is NO or PARTIAL. A well-evidenced platform that has never been deployed is
exactly that.

---

## 1. What was changed

The platform gained the object it did not have: **a decision**.

Before this work, approvals attached to *runs*. A run records what the machine did; it
starts and finishes within minutes. A decision records what the organisation chose, on
what evidence, and whether it worked — and whether it worked is knowable weeks later,
long after every run involved has ended. Conflating the two is why the platform could
not answer its own North Star question.

| Area | Change |
|---|---|
| Data | Migration `0013`: 15 tables — `domains`, `teams`, `team_members`, `decisions`, `decision_options`, `recommendations`, `decision_evidence`, `decision_transitions`, `actions`, `kpi_definitions`, `kpi_values`, `decision_outcomes`, `lessons_learned`, `notifications`, `policy_results` |
| Identity | Three new system roles — `engineer`, `section_lead`, `department_manager` — for the three personas that had none |
| Engine | `agentic_os.decisions`: an 11-state lifecycle, a confidence calculator, an effectiveness calculator, a domain-scoped repository |
| API | 13 endpoints under `/v1/decisions`, `/v1/kpis`, `/v1/notifications` |
| Console | Decision Queue, Decision Case, Inbox, and an executive band above platform health on the landing surface |
| Assurance | 10 new controls (3 critical), catalogue now 70 controls / 136 weighted points |
| Tests | 428 tests, up from 335 |

## 2. What was removed

**Nothing.** The brief said not to rebuild blindly, and the audit found nothing dead to
remove: all 30 existing routes render real API data, and none were placeholders. Two
navigation entries moved (Command Center and Approvals now sit under a new *Decide*
group) and one heading was renamed ("Requires attention" → "Platform requires
attention"). No route was deleted, no component was replaced, and the visual identity is
untouched.

## 3. What was redesigned

* **The landing surface's ordering.** The Command Center kept every panel it had and
  gained a decisions band above them. Run counts and control scores answer "is the
  platform healthy?"; people open this product to ask "what needs me, and did what we
  did work?". Both questions stay on the page; only the order changed.
* **Navigation.** Six groups organised by system component became seven, led by *Decide*.
* **The demo seed.** It now drives the lifecycle engine instead of writing states around
  it, so the seeded cases carry real transitions, real audit ledger entries and real
  notifications.
* **Console redirects.** All five route handlers now emit a path-only `Location`
  (see §6.3).

## 4. What was added

Beyond the table in §1, three things that are the point of the exercise:

**Confidence is computed or absent.** Four countable inputs — evidence volume saturating
at five, freshness, source authority, and the score gap between the top two options —
weighted and summed. Where the inputs do not support a figure the answer is `null` and
every surface renders **"Confidence: Not Calculated"** with the reason. Not 0%, which
reads as certainly-wrong; not a floor, which is an invented number wearing a modest hat.
A database CHECK refuses to store a figure without the inputs that produced it, and the
API has no confidence field on its request body, so a caller cannot supply one.

**A domain boundary that exists.** The audit found `agents.domain` was an unconstrained
string, so the requirement that cross-domain access be zero had nothing to enforce
against. Domain membership is now a join inside the query, and a non-member receives
`404` — not `403`, because a 403 on a specific identifier confirms the identifier names
something real.

**Separation of duties.** REVIEW and APPROVE are distinct stations: a section lead
reviews and cannot approve. An agent may advance its own analysis and may never carry a
case past a human station. And the platform administrator — who is granted every
permission in the catalogue — is explicitly excluded from authority over business
decisions.

## 5. What was tested

| Suite | Tests | What it proves |
|---|---:|---|
| `tests/decisions/test_schema_guarantees.py` | 20 | Constraints hold against a real database, probed by attempting what they forbid |
| `tests/decisions/test_lifecycle.py` | 17 | All 121 ordered state pairs; permissions; MFA; the single-writer rule |
| `tests/decisions/test_confidence_and_effectiveness.py` | 15 | Both calculators, and both empty cases |
| `tests/decisions/test_domain_isolation.py` | 10 | Cross-domain access at repository and query level |
| `tests/decisions/test_decision_api.py` | 25 | The HTTP layer: 401, 404-not-403, 409, 403 |
| `tests/i18n/test_console_redirects.py` | 9 | No route rebuilds a redirect from the bind address |
| **Total suite** | **428** | 0 failures, **0 skipped** with `AGENTIC_REQUIRE_SERVICES=db,redis` |

**Three guards were verified by deliberate breakage**, not by observing a pass:

| Guard | Break applied | Result |
|---|---|---|
| Domain predicate | `if sees_all_domains(ctx)` → `if True` | 5 of 10 isolation tests failed |
| Single lifecycle writer | Planted `UPDATE decisions` in `repository.py` | Guard named the file |
| Absolute redirects | Restored `NextResponse.redirect(new URL(...))` in one route | Guard named the route |

## 6. What was runtime-verified

Against a live PostgreSQL 16 + pgvector, a live FastAPI process, and a real browser.

### 6.1 A decision through all eight loop stages

```
DETECT     201  VERIFY-dd0e4391
ANALYSE    options and evidence attached, moved to ANALYSING
RECOMMEND  201  confidence=72%          <- computed server-side, not supplied
REVIEW     engineer self-reviewing -> HTTP 409
           section lead reviewing  -> HTTP 200
APPROVE    section lead approving  -> HTTP 403
           department manager      -> HTTP 200
EXECUTE    dispatched and completed
VERIFY     outcome recorded        -> HTTP 201
LEARN      lesson recorded         -> HTTP 201

final state        CLOSED
confidence         72%  from 4 inputs
transitions logged 10
outcome            ACHIEVED
effectiveness      50%  (1/2)
```

The two refusals are the separation of duties working: an engineer cannot review their
own case, a section lead cannot approve what they reviewed.

### 6.2 Cross-domain isolation

```
Signalling lead's queue:      DEC-2026-0041, DEC-2026-0022
Rolling stock lead's queue:   DEC-2026-0038
Signalling lead opening a rolling stock decision:  HTTP 404
```

### 6.3 The console, in a browser

Both new surfaces render real data. The case page shows the confidence calculation
expanded — each input's measured value, normalised value and weight. And the thin case
renders, verbatim:

> **Confidence** Not Calculated — fewer than two scored options, so there is no
> separation to measure. A number has not been substituted.

### 6.4 Load

Every scenario at 100% success across concurrency 1, 8 and 32, including the two new
decision endpoints:

| Scenario | p95 @ 1 | p95 @ 8 | p95 @ 32 |
|---|---:|---:|---:|
| decision_queue | 17.3 ms | 92.5 ms | 462 ms |
| decision_effectiveness | 16.1 ms | 160 ms | 493 ms |
| command_center | 24.0 ms | 169 ms | 749 ms |

### 6.5 Disaster recovery

The exercise dumps the live database, restores it into a scratch database, compares
every table, re-hashes the audit chain *inside the restored copy*, and drops the scratch
database afterwards:

```
outcome=SUCCESS  rpo=0s  rto=1s  rows_verified=12250
96 tables and 12250 rows matched, and 4459 audit entries across 2 tenants
re-hashed intact in the restored database
```

This corrects an earlier draft of the audit, which recorded DR as unverified on the
assumption that no maintenance identity was configured. One is, and the exercise runs.

### 6.6 Accessibility

Twenty-five surfaces × two colour schemes × two text directions = **100 scans, zero
violations of any impact**, including the two new surfaces in Arabic right-to-left.

That number is only trustworthy because the audit now refuses to produce one it did not
earn — see §6.6.

### 6.7 The audit that would not lie

Worth recording because it is the whole discipline in one incident. An earlier run of the
accessibility audit reported *zero serious violations* across 100 scans. Half of those
scans were of the sign-in page: the second browser context's login had been refused by
the API's rate limiter, every surface had redirected to `/login`, and axe had cheerfully
scanned that twenty-five times.

The script already refused to proceed on a text-direction mismatch, with a comment
observing that one "would make every RTL result meaningless while still reporting zero
violations". The authentication case is that same sentence and was not guarded.

It is now. The guard fired on the next run and refused to write a report at all — which
in turn exposed the redirect defect in §4 that was causing the sign-in to fail. The
100-scans-zero-violations figure above was produced only after both were fixed.

## 7. What was NOT verified

Stated plainly, with the marker the brief asks for.

| Item | Marker | Why |
|---|---|---|
| Disaster recovery at production scale | `NOT_VERIFIED` | The exercise runs and passes here (see §6.5); a 12,250-row restore in one second says nothing about a terabyte across availability zones |
| Seven outbound integrations (ERP, payment rail, mail relay, CRM, MAXIMO, asset register) | `EXTERNAL_DEPENDENCY` | No credentials, no sandboxes, no backing systems. They remain `NOT_IMPLEMENTED` and the planner refuses to plan with them |
| Penetration test | `EXTERNAL_DEPENDENCY` | Requires an external party |
| Screen reader verification | `EXTERNAL_DEPENDENCY` | Requires a human using assistive technology |
| Arabic page bodies | `BLOCKED` | Needs a native reviewer. Machine-translating governance and railway-maintenance terminology would read as authoritative and be wrong. Chrome is translated; a notice in Arabic says the body is not |
| `knowledge_sources` table | `NOT_IMPLEMENTED` | Named in the brief's minimum model. `documents` and `datasets` already carry `source_system`, and a registry with no consumer would be a decorative control |
| Production deployment | `NOT_VERIFIED` | Nothing has been deployed. Every claim about production behaviour is unproven |
| Secret rotation executed | `NOT_VERIFIED` | Designed, never run |
| Test coverage percentage | `NOT_VERIFIED` | Not measured |
| Threat model for the decision layer | `NOT_IMPLEMENTED` | Not written |
| OpenTelemetry export | `NOT_IMPLEMENTED` | Traces are recorded to Postgres and not exported; nobody will query that table during an incident |
| Frontend unit tests | `NOT_IMPLEMENTED` | The console is covered by build, axe, and the API tests behind it — not by component tests |

## 8. Remaining risks

1. **Nothing here has run in production.** Every performance figure comes from one
   machine with one dataset. The database holds four seeded decisions; behaviour at
   four hundred thousand is unmeasured.
2. **Observability is recorded but not operable.** Traces, costs and metrics land in
   tables. There are no dashboards, no alert routing, no SLO definitions in code. The
   data would support an investigation and will not surface a problem to a human
   unprompted.
3. **The append-only guarantee has a stated boundary.** The table owner can
   `ALTER TABLE … DISABLE TRIGGER` and delete. This is inherent to PostgreSQL and
   applies equally to the audit ledger. Mitigated by the owner not being the
   application role; **not** claimed as tamper-proof against a superuser.
4. **A decision with history cannot be deleted, and neither can its tenant.** Intended —
   a record whose history can be erased is not a record — but it means tenant
   offboarding is a documented procedure, not a `DELETE`. Erasure pseudonymises, as it
   already does for the ledger.
5. **The KPI framework has definitions and no values.** `kpi_values` is empty because
   nothing computes it yet. The console says "Not measured yet" rather than showing
   zero, which is honest but is not the same as working.
6. **The cross-domain exemption is a list.** Three roles see every domain. That list
   growing quietly is how "zero cross-domain access" stops being true, so a test pins
   it — but a test cannot judge whether a fourth role belongs there.

## 9. Next actions

**Before any production use**

1. Repeat the restore exercise against a production-sized dataset and topology. It
   passes here in one second on 12,250 rows, which proves the mechanism and nothing
   about the scale.
2. Commission an external penetration test of the decision surfaces.
3. Write the tenant offboarding procedure that risk 4 requires.
4. Export traces to a collector so an incident can be investigated.

**To make the KPI framework real**

5. Implement the computation behind each `kpi_definitions.formula` and populate
   `kpi_values` on a schedule. Until then the executive surface reports definitions,
   not measurements.

**To close the remaining product gaps**

6. Component tests for the decision surfaces.
7. A native Arabic reviewer for page bodies.
8. Alert routing: assignment, acknowledgement, escalation.

---

## 10. Area — Before — After — Evidence — Status

| Area | Before | After | Evidence | Status |
|---|---|---|---|---|
| Decision object | Absent | 15 tables, RLS forced, append-only history | `test_every_new_table_forces_row_level_security` | DONE |
| Lifecycle | Absent | 11 states, one writer, server-enforced | 121 pair assertions; single-writer scan | DONE |
| AI confidence | Not displayed | Computed from 4 inputs, or "Not Calculated" | DB CHECK + 8 tests + browser render | DONE |
| North Star KPI | Uncomputable | Decision Effectiveness Rate, null over an empty set | `test_the_rate_is_not_calculated_over_an_empty_set` | DONE |
| Domain boundary | A string column | An entity, joined into every query | 404 observed live; 5 tests fail without it | DONE |
| Personas served | 4 of 7 | 7 of 7 | 3 new roles with distinct permission sets | DONE |
| Separation of duties | Approve only | Review ≠ approve; admin ≠ business authority | `BUSINESS_DECISION_AUTHORITY`; 2 live refusals | DONE |
| Human stations | 1 of 5 | 4 of 5 (analysis handoff still implicit) | Lifecycle tests | PARTIAL |
| Notifications | None | Routed by permission ∩ domain membership | 3 API tests | DONE |
| KPI framework | 6-column samples | Definitions with formula, unit, direction, target | `test_every_kpi_carries_its_definition` | PARTIAL — no values |
| Outcome verification | Unlinked ROI records | Target vs actual, verifier, method, verdict | DB constraint + live loop | DONE |
| Learning | Absent | `lessons_learned`, linked to the decision | Live loop; case page | DONE |
| Console redirects | Absolute, bind-address host | Path-only | Guard + observed `location: /` | DONE |
| Accessibility | 23 surfaces | 25 surfaces, audit cannot pass unauthenticated | axe + auth guard | DONE |
| Cross-tenant isolation | Proven | Proven, extended to the new tables | Rebind test | DONE |
| Disaster recovery | Runs, unmeasured at scale | Runs, evidenced | RPO 0s, RTO 1s, 96 tables, 4,459 ledger entries re-hashed | PARTIAL |
| Outbound integrations | 7 NOT_IMPLEMENTED | 7 NOT_IMPLEMENTED | Capabilities register | BLOCKED |

## 11. Requirement — Implemented — Tested — Runtime Verified — Production Ready

| Requirement | Implemented | Tested | Runtime Verified | Production Ready |
|---|---|---|---|---|
| Decision case lifecycle, 11 states | YES | YES | YES | NO |
| Server-side transition enforcement | YES | YES | YES | NO |
| DETECT → ANALYSE → RECOMMEND | YES | YES | YES | NO |
| REVIEW → APPROVE | YES | YES | YES | NO |
| EXECUTE → VERIFY → LEARN | YES | YES | YES | NO |
| Decision Effectiveness Rate | YES | YES | YES | NO |
| Confidence calculated, never invented | YES | YES | YES | NO |
| "Confidence: Not Calculated" where uncomputable | YES | YES | YES | NO |
| Cross-domain access = zero | YES | YES | YES | NO |
| Authorization enforced server-side | YES | YES | YES | NO |
| No non-empty-password shortcut | YES | YES | YES | NO |
| Executive Command layer | YES | PARTIAL | YES | NO |
| Decision Workbench layer | YES | PARTIAL | YES | NO |
| Platform Administration layer | YES | YES | YES | NO |
| 7 target user roles | YES | YES | PARTIAL | NO |
| Notifications | YES | YES | YES | NO |
| KPI definitions | YES | YES | YES | NO |
| KPI values | NO | NO | NO | NO |
| Agents cannot act unsupervised | YES | YES | YES | NO |
| No hidden chain-of-thought exposed | YES | YES | YES | NO |
| Append-only decision history | YES | YES | YES | PARTIAL |
| Hash-chained audit ledger | YES | YES | YES | PARTIAL |
| Row level security, forced | YES | YES | YES | PARTIAL |
| Page load < 2s | YES | NO | PARTIAL | NO |
| API p95 < 300ms | YES | YES | YES | NO |
| Search p95 < 1s | YES | YES | YES | NO |
| Critical audit event loss = 0 | YES | YES | PARTIAL | NO |
| Availability ≥ 99.9% | NO | NO | NO | NO |
| Disaster recovery verified | YES | YES | YES | NO |
| Outbound enterprise integrations | NO | NO | NO | BLOCKED |
| Arabic page bodies | NO | NO | NO | BLOCKED |
| Production deployment | NO | NO | NO | NO |

**Every cell in the Production Ready column is NO or PARTIAL, and that is the honest
answer.** Nothing in this repository has been deployed, disaster recovery has never been
exercised here, and no external party has reviewed it. The platform is well built and
well evidenced; it is not proven in production, and no score should be read as saying
otherwise.
