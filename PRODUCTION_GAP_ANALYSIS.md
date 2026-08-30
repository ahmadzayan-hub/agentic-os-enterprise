# Production Gap Analysis

**Input:** `CURRENT_STATE_AUDIT.md` (commit `2148c0e`)
**Priority scheme:** P0 blocks the product's core promise · P1 blocks a named persona ·
P2 degrades quality · P3 deferred with a stated reason

Every gap states how it will be **proven closed**. A gap with no closure test is not a
gap, it is an opinion.

---

## P0 — Blocks the core promise

The product's promise is the operating loop. These gaps mean the loop cannot run.

### P0-1 · No decision object exists

**Gap.** Nothing in the system represents an organisational decision. Approvals attach to
runs; a run is what the machine did, not what the organisation chose. The 11-state
lifecycle the directive specifies has no storage.

**Impact.** DETECT, ANALYSE, RECOMMEND, VERIFY and LEARN cannot be recorded. The North
Star KPI — Decision Effectiveness Rate — is uncomputable, because there is no denominator.

**Closure.** `decisions`, `decision_options`, `recommendations`, `decision_evidence`,
`decision_transitions` tables under RLS; a lifecycle engine that rejects every illegal
transition; tests asserting each of the 11 states and that illegal transitions raise.

### P0-2 · Decision state transitions are not server-enforced

**Gap.** By extension of P0-1 — no state machine exists at all.

**Impact.** Without server-side enforcement, any client could move a case from Detected
straight to Verified. The directive is explicit: UI hiding alone is not authorization.

**Closure.** A single `transition()` function is the *only* write path to
`decisions.state`; a DB CHECK constrains the state vocabulary; a trigger records every
transition append-only; tests assert that a forbidden transition raises and that no other
code path writes the column.

### P0-3 · No outcome verification, so no learning

**Gap.** `business_outcomes` exists but has no link to a decision, no target, no
verification, no verifier, no verified-at. `lessons_learned` does not exist.

**Impact.** The system cannot answer whether a decision worked. VERIFY and LEARN are
absent, and the North Star KPI is unanswerable even with P0-1 closed.

**Closure.** `decision_outcomes` linked to `decisions` with target, actual, verification
method, verifier and timestamp; `lessons_learned` linked to the decision; a Decision
Effectiveness Rate computed **only** from verified outcomes; a test asserting the rate is
`Not Calculated` when the denominator is zero rather than reporting 0% or 100%.

### P0-4 · Confidence has no calculation behind it

**Gap.** No confidence is displayed anywhere — correct today, but the moment a
recommendation surface exists there will be pressure to put a percentage on it.

**Impact.** An invented confidence figure is the single most damaging thing this product
could ship. It is authoritative-looking and unfalsifiable.

**Closure.** Confidence is a **derived** value computed from countable inputs (evidence
count, evidence recency, source authority, option separation) with the calculation stored
alongside it and rendered to the user. When inputs are insufficient the API returns
`null` and the UI renders **"Confidence: Not Calculated"**. Tests assert that no code path
can produce a confidence value without a stored calculation, and that a decision with no
evidence yields `null` — not zero.

### P0-5 · No domain boundary exists

**Gap.** `agents.domain` is an unconstrained string. There is no `domains` table, no
domain membership, no domain-scoped authorization.

**Impact.** The directive's hard target — unauthorized cross-domain data access = ZERO —
has nothing to enforce against. Cross-*tenant* isolation is proven; cross-*domain* is not
implemented.

**Closure.** `domains` and `team_members` tables; domain scope on decisions; authorization
that requires domain membership **in addition to** permission; a test that a user in
domain A receives 404 (not 403 — existence itself is not disclosed) for a decision in
domain B, and that the query returns zero rows rather than filtering after fetch.

### P0-6 · No KPI framework

**Gap.** `metric_samples` has no definition registry, no target, no owner, no direction,
no unit semantics.

**Impact.** Executives cannot be shown anything defensible. Any KPI rendered today would
be a number without a definition, which is a fake KPI by the directive's own definition.

**Closure.** `kpi_definitions` (formula, unit, direction, target, owner, source) and
`kpi_values` (value, period, computed-from); every displayed KPI traceable to its
definition; a test that a value cannot be inserted without a definition.

### P0-7 · Three of seven personas have no product

**Gap.** Engineer, Section Lead and Department Manager have neither a role nor a surface.

**Impact.** The people who make operational decisions cannot use the decision product.

**Closure.** `engineer`, `section_lead`, `department_manager` system roles with distinct
permission sets and autonomy ceilings; a Decision Workbench serving them; tests asserting
each role's permission set is distinct and that a section lead cannot approve above their
authority.

---

## P1 — Blocks a named persona or a stated requirement

### P1-1 · No notification of any kind
A pending approval is discovered by opening the console and looking. **Closure:**
`notifications` table with recipient, kind, subject, read state, and a decision link;
generated on state transitions that require a human; an inbox surface; a test that a
transition into `Awaiting Approval` creates exactly one notification per eligible
approver and none for anyone else.

### P1-2 · Only one of five human stations exists
Reviewers cannot annotate, request analysis, or propose an alternative. **Closure:**
review notes on a case, reviewer-requested re-analysis as a legal transition, options
proposable by a reviewer; tests per station.

### P1-3 · Executive Command shows platform metrics, not decisions
**Closure:** an executive surface whose primary content is decisions requiring attention,
decision effectiveness, and KPI status — with platform health demoted to a secondary
panel.

### P1-4 · Policy results are not persisted
"Show every decision this policy affected" is unanswerable. **Closure:** `policy_results`
rows written on every evaluation, linked to the decision; a test that an evaluation
produces exactly one row.

### P1-5 · No governed action record
Executing a decision produces run steps, not a business action with an owner and a
reversal path. **Closure:** `actions` table linked to the decision, with executor,
status, reversibility and result.

### P1-6 · Alerts have no routing — **CLOSED**
Recorded as "no assignee, no acknowledgement, no escalation". That understated it: the
acknowledgement columns *did* exist, and the real gap was that the `alerts` table had
held no row since it shipped in migration 0006 — nothing anywhere raised one, and the
only statement touching it was a read.

**Closed by** migration 0015, `observability/alerting.py` (five registered rules,
deduplication, resolution, permission-and-domain routing, escalation), the `/v1/alerts`
routes, the `/operations/alerts` console surface, and a five-minute schedule inside the
worker so a pass runs without anybody asking. 69 tests; each guard verified by
deliberate breakage. Evidence: `SECURITY_REVIEW.md` §1.14–1.19,
`FINAL_READINESS_REPORT.md` risk 2.

### P1-7 · No frontend tests
Build + axe only. **Closure:** at minimum, tests for the decision surfaces' authorization
behaviour and empty-state differentiation.

---

## P2 — Quality degradation

| # | Gap | Closure |
|---|---|---|
| P2-1 | No performance SLO asserted in CI | Assert API p95 < 300 ms on the decision endpoints in the load test, failing the job on breach |
| P2-2 | Empty states do not distinguish "no data" from "not permitted" | Distinct rendering, tested |
| P2-3 | No pagination contract | Cursor pagination on decision collections |
| P2-4 | No design-token layer | Extract tokens from the existing stylesheet without changing the visual identity |
| P2-5 | Test coverage unmeasured | Measure; publish; do not gate yet |
| P2-6 | No OpenTelemetry export | Export spans; `EXTERNAL_DEPENDENCY` for a collector |
| P2-7 | Long route modules | Extract a service layer for decisions from the start |
| P2-8 | Page bodies untranslated | Extend the catalogue to the new decision surfaces so they ship bilingual from day one |

---

## P3 — Deferred, with reasons

| # | Gap | Why deferred |
|---|---|---|
| P3-1 | Seven outbound integrations | `EXTERNAL_DEPENDENCY` — ERP, payment rail, mail relay, CRM, MAXIMO, asset register. No credentials, no sandboxes, no backing systems in this environment. Implementing them would mean writing code that has never run against the system it claims to integrate with. They stay `NOT_IMPLEMENTED`. |
| P3-2 | DR verification | `PRODUCTION_CONFIGURATION_REQUIRED` — needs `AGENTIC_DR_ADMIN_URL`. The exercise is real and refuses to fake evidence. |
| P3-3 | Penetration test | `EXTERNAL_DEPENDENCY` — requires an external party. |
| P3-4 | Arabic body translation | `BLOCKED` on a native reviewer. Machine translation of governance terminology would read as authoritative and be wrong. |
| P3-5 | Multi-region | Not required by any stated requirement. |
| P3-6 | Screen reader verification | `EXTERNAL_DEPENDENCY` — requires a human using assistive technology. |
| P3-7 | Model drift detection | Needs production traffic that does not exist. |

---

## Closure summary

| Priority | Count | In scope now |
|---|---:|---|
| P0 | 7 | **All 7** |
| P1 | 7 | All 7 |
| P2 | 8 | 5 of 8 (P2-4, P2-5, P2-6 partial) |
| P3 | 7 | **0** — each is `EXTERNAL_DEPENDENCY`, `BLOCKED` or `PRODUCTION_CONFIGURATION_REQUIRED` and will be reported as such, not silently omitted |
