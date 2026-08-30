# Security Review — Decision Intelligence Layer

**Scope:** the changes made under the Decision Intelligence directive, assessed against
the existing platform's security model. Commit range `545f9f2..HEAD` on
`claude/agentic-os-enterprise-v3.1-ogi9cq`.

**Posture:** adversarial. Every control below was probed by attempting the thing it
forbids, and the probe output is quoted or referenced. A control that was only read and
believed is marked `NOT_VERIFIED`.

---

## 1. Findings

### 1.1 The confidence constraint was inert as first written — **FIXED, verified**

**Severity:** High (integrity of the platform's most consequential displayed figure)

`recommendations` carries a CHECK intended to make an unsupported confidence figure
impossible to store. As first written:

```sql
CHECK (confidence IS NULL OR jsonb_typeof(confidence_calculation -> 'inputs') = 'array')
```

Against the column's own default of `'{}'`, `-> 'inputs'` yields SQL NULL,
`jsonb_typeof(NULL)` yields NULL, and a CHECK constraint rejects only on FALSE. The guard
was therefore inert for **exactly the value it existed to catch**. Demonstrated against
the live database before the fix:

```
jsonb_typeof('{}'::jsonb -> 'inputs') = 'array'         -> None
INSERT INTO recommendations (..., confidence) VALUES (..., 0.87)   -> ACCEPTED
```

**Fix:** coalesced, and a non-empty array now required. Re-probed: the same insert is
refused, as are `{"inputs":"trust me"}`, `{"inputs":[]}` and `{"inputs":{}}`.
Regression-covered by `tests/decisions/test_schema_guarantees.py`, four parametrised
cases plus the default case.

### 1.2 Platform administrators would have acquired business decision authority — **FIXED, verified**

**Severity:** High (separation of duties)

`platform_admin` is granted `tuple(p.id for p in CATALOGUE)` — every permission in the
catalogue. Adding `decisions:review`, `decisions:approve`, `decisions:execute` and
`decisions:verify` to that catalogue would silently have given whoever administers the AI
platform the authority to approve the organisation's business decisions. No audit trail
repairs that after the fact.

**Fix:** `BUSINESS_DECISION_AUTHORITY` is excluded from the blanket grant.

```
platform_admin       none
security_admin       none
department_manager   ['decisions:approve', 'decisions:execute', 'decisions:review', 'decisions:verify']
section_lead         ['decisions:review', 'decisions:verify']
engineer             none
```

Covered by `DEC-007`, and by `test_a_platform_administrator_does_not_see_across_domains`.

### 1.3 The domain predicate is inside the query — **verified by deliberate breakage**

**Requirement:** unauthorized cross-domain data access = ZERO.

Domain membership is a subquery in the `WHERE` clause, not a filter applied to fetched
rows. Verified two ways:

* At the repository level, a SQL `count(*)` under the same predicate returns `0` for a
  non-member — the database never found the row.
* Over HTTP, the response is **404, not 403**. A 403 against a specific identifier
  confirms the identifier names something real, which is the disclosure the brief
  forbids.

The guard was then deliberately neutralised (`if sees_all_domains(ctx)` → `if True`) and
**five of the ten isolation tests failed**, including the two that matter most:
`test_a_non_member_is_told_the_decision_does_not_exist` and
`test_naming_the_other_domain_explicitly_does_not_widen_access`. Restored, all ten pass.

Confirmed again at runtime against a live API: the signalling lead requesting a rolling
stock decision received `HTTP 404`.

### 1.4 The lifecycle has a single writer — **verified by deliberate breakage**

A state machine spread across call sites is not a state machine. A source-level test
scans every module in the package for `UPDATE decisions`. Planting a rogue writer in
`repository.py` produced:

```
assert ['decisions/repository.py'] == []
```

The same guard caught my own seed module writing states directly, which is why the demo
data now runs through the engine and carries real audit entries.

### 1.5 The transition log is append-only — **verified, with a stated limit**

UPDATE, DELETE and TRUNCATE on `decision_transitions` are refused by triggers, for the
application role and the provisioning role alike, and the TRUNCATE guard is
`FOR EACH STATEMENT` because a row trigger would not fire — the same hole migration 0009
found in the audit ledger.

**Stated limit, not a defect:** the *table owner* can `ALTER TABLE … DISABLE TRIGGER` and
then delete. This is inherent to PostgreSQL and applies equally to `audit_events`. The
mitigation is that the owner role is not the application role, holds separate
credentials, and is not reachable from the request path. I had to perform exactly this
manoeuvre to clean test rows out of the development database, which is a fair
demonstration of both the strength and the boundary of the control. **Not** claimed as
tamper-proof against a database superuser.

### 1.6 A consequence: decisions and their tenants cannot be plainly deleted — **accepted, documented**

Because the trigger fires on cascaded deletes, a decision with any transition history
cannot be removed with `DELETE`, and neither can its tenant. This is the same property
`audit_events` has carried since migration 0009 and it is intended: a decision record
whose history can be erased is not a record.

Subject erasure therefore pseudonymises rather than deletes here.
`decision_transitions` is registered in `agentic_os.privacy.dsar.NON_ERASABLE` with a
stated reason, so a DSAR reports it as retained rather than silently skipping it.

### 1.7 Confidence cannot be supplied by a caller — **by construction**

`NewRecommendation` has no confidence field. The value is computed server-side from
evidence and options the caller has already stored. If a caller could pass one, the
calculation would be advisory and the first integration under deadline pressure would
post `0.95`.

### 1.8 Verification cannot be attributed to someone else — **by construction**

`verified_by_user_id` and `verified_at` are taken from the authenticated context, not the
request body. The schema additionally refuses any verdict other than `PENDING` without a
verification method and timestamp.

### 1.9 An agent cannot carry a decision past a human station — **verified**

An agent principal may advance `ANALYSING`, `RECOMMENDATION_READY` and `AWAITING_REVIEW`.
It is refused at `AWAITING_APPROVAL`, `APPROVED`, `EXECUTING` and `VERIFIED`. This is the
same shape as the constitution's rule that the conductor never executes production tools:
the machine prepares, a person commits.

### 1.10 Notifications do not leak the existence of foreign work — **verified**

Recipients are derived from holding the permission the next step needs **and** belonging
to the decision's domain. Notifying every permission holder tenant-wide would disclose
that decisions exist in domains the recipient cannot open.

Verified: an engineer's inbox contains no `APPROVAL_REQUESTED` or `REVIEW_REQUESTED`
item, and marking a lead's notification read as an engineer returns `updated: 0`.

### 1.11 Console redirects named an origin, not a path — **FIXED, verified**

**Severity:** Medium (session integrity; open-redirect surface behind a proxy)

Every console route handler built its redirect as `new URL(path, request.url)`. In the
standalone server `request.url` carries the *bind* address, so a console reached on
127.0.0.1 sent the browser to `http://0.0.0.0:3000/` — a different origin, which meant
the session cookie set on that very response was not sent with the follow-up request and
the user bounced straight back to the sign-in page. Behind a reverse proxy the same
construction takes its host from whatever upstream passes along, which is the classic
host-header redirect problem.

Observed before the fix:

```
console login: 303
location: http://0.0.0.0:3500/
```

**Fix:** `lib/redirect.ts` emits a path-only `Location`, which the client resolves
against the request it actually made and which cannot name another origin at all. All
five console route handlers converted. After:

```
login: 303
location: /
```

Guarded by `tests/i18n/test_console_redirects.py`, which scans every route handler for
the two constructions that reintroduce it and was confirmed to fail when one was
restored.

### 1.12 The accessibility audit could report a clean run it never performed — **FIXED**

**Severity:** Medium (evidence integrity)

This is the project's recurring failure mode in its own tooling. If the audit's sign-in
failed, every surface redirected to `/login`, axe scanned the login page twenty-five
times, and the report said "0 serious violations" about pages it had never loaded. It
happened here: the API rate limiter refused the second browser context's login after the
first pass had spent the budget, and half the report described the sign-in page.

The script already guarded text direction with a comment saying a mismatch "would make
every RTL result meaningless while still reporting zero violations" — the authentication
case is exactly that and was not guarded.

**Fix:** the audit now fails loudly if it is still on `/login` after signing in, and each
authenticated surface confirms it did not redirect. Verified: the guard fired and
refused to write a report.

### 1.13 MFA replay protection is real, and the tests do not defeat it — **verified**

Signing in twice as an MFA-required user inside one 30-second window genuinely fails.
The suite caches tokens per run and, when an earlier module has spent the current code,
waits for the next period. Disabling the control to make the suite convenient would have
left it green with the control off.

---

### 1.14 `/v1/incidents` returned every alert in the tenant — **FIXED, verified**

The operations surface listed alerts with no domain or permission filter at all:

```sql
SELECT ... FROM alerts WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 100
```

This was written when `alerts` had never held a row and could not, so it disclosed
nothing. Building the alerting engine turned it into a cross-domain disclosure in the
same commit that made alerts real — a signalling engineer would have been shown that
rolling stock had a CRITICAL problem, and an alert requiring `audit:verify` would have
been readable by anyone holding `incidents:read`.

The fix moved the visibility predicate out of the new router and into the engine
(`observability/alerting.visibility_predicate`) so both readers apply the same one, and
the predicate is in the WHERE clause rather than a filter after retrieval. **Verified by
deliberate breakage**: reverting `/v1/incidents` to the unfiltered query fails
`test_the_incidents_surface_applies_the_same_boundary` and nothing else.

The general lesson is worth keeping: a boundary applied by one of two readers of the
same table is not a boundary, and the older reader is the one that gets missed.

### 1.15 The alert list's counts had to be taken under the same predicate — **verified**

A count is a disclosure. "You have 3 alerts" beside a list of one tells the reader two
exist that they may not see, which is the fact the boundary exists to withhold — only
the wording is kept back. Both the listing and its totals run under the same predicate,
and `test_the_counts_are_taken_under_the_same_boundary_as_the_list` asserts they agree
for four different principals. A second test asserts the engineer's total is *smaller*
than the auditor's, so the first cannot pass by filtering nothing.

### 1.16 Alert routing requires membership, permission, and an unexpired grant — **verified by deliberate breakage**

Assignment picks a candidate who holds the permission the alert names, belongs to its
domain where it has one, and whose role grant has not expired. Each of the three was
removed in turn and the tests confirmed to fail:

| Removed | Test that failed |
|---|---|
| domain membership | `test_a_domain_with_no_qualifying_member_leaves_the_alert_unassigned` |
| unexpired grant | `test_an_expired_role_grant_does_not_receive_alerts` |
| the permission clause | `test_an_alert_requiring_a_permission_the_caller_lacks_is_not_returned` |

Where no candidate satisfies all three the alert stays **visibly unassigned**. The
tempting fallback — give it to an administrator — routes it to somebody who cannot act
and marks it owned, which is worse than leaving it plainly nobody's.

### 1.17 An oversight role can see every alert and silence none — **verified**

`auditor` holds the widest read in the platform, spanning every domain. It holds no
write. Granting acknowledgement alongside that visibility would let the one role that
can see every alert also stop every alert escalating.
`test_an_oversight_role_cannot_acknowledge_or_trigger` pins both halves.

### 1.18 Acknowledging an invisible alert is 404 and changes nothing — **verified**

403 on a specific identifier confirms the identifier is real, and here would also
confirm that an alert exists in a domain the caller cannot open. The route checks
visibility first and reports absence. The test also asserts the alert is left
**untouched**: acknowledging it would stop its escalation, which is a denial of service
against whoever should have seen it.

### 1.19 A crashed alert rule must not read as "the condition cleared" — **verified by deliberate breakage**

A rule that raises is recorded in `failed_rules`, is surfaced in the worker's errors,
and does **not** resolve the alerts it previously raised. Changing the `except` branch
to treat a crash as an empty finding list fails
`test_a_crashed_rule_does_not_resolve_its_own_alerts`. This is a security property as
much as an operational one: closing a live alert because the code that noticed it broke
is the one direction that error must never take.

### 1.20 Console middleware named an origin, not a path — **FIXED, partially mitigated**

Finding 1.11 fixed absolute redirects in the route handlers via `lib/redirect.ts`. The
**middleware** was missed and still built redirects from `request.url`, which in the
standalone server carries the host Next resolved rather than the one the browser asked
for. A console reached on `127.0.0.1:3036` was redirected to `http://localhost:3036/` —
a different origin, so the session and locale cookies just set were not sent with the
follow-up request.

The accessibility audit is what caught it: its right-to-left pass rendered `dir="ltr"`
because the locale cookie had been dropped in exactly that way, and the audit fails
loudly on a direction mismatch instead of scanning on regardless. That guard was added
under finding 1.12 for a different reason and paid for itself here.

Redirects are now built from the forwarding headers, so **behind a proxy the browser is
returned to the host it actually used**. Measured limitation, stated rather than
assumed: when the reconstructed origin carries the same port Next is listening on, Next
rewrites the Location host to its own resolved hostname regardless. Serving on
`127.0.0.1:3037` and asking for `127.0.0.1:3037` still yields `localhost:3037`; asking
for `127.0.0.1:9999` is left alone. No middleware code can prevent that, so it is
recorded as a deployment constraint — reach the console on the hostname it is served
under — rather than claimed as fixed.

### 1.21 The accessibility audit's sign-in guard was too narrow — **FIXED**

Finding 1.12 added a guard that the audit had actually signed in, checking the browser
was no longer on `/login`. That test let a different failure through: when the API was
unreachable the sign-in route threw, the browser stopped on a 500 at
`/api/session/login` — which does not start with `/login` — and the audit carried on to
scan twenty-five redirects to the sign-in page and report them clean. Observed, not
theorised: it happened during this work.

The guard now asserts the browser landed on the console (`/`), not merely that it left
one particular page. A guard that only rules out the failure you thought of is how this
audit lies.

## 2. Controls carried over unchanged

The decision layer creates no new authentication, authorization, audit or approval
mechanism. It reuses:

| Control | Evidence |
|---|---|
| `FORCE ROW LEVEL SECURITY`, no bypass predicate | `test_every_new_table_forces_row_level_security` — all 15 new tables |
| Transaction-local `app.tenant_id` | `test_a_second_tenant_cannot_see_another_tenants_decisions` |
| Hash-chained audit ledger | every transition writes an entry; 10 entries observed for the runtime loop |
| `require_permission` as a route dependency | all 13 new endpoints |
| Argon2id + TOTP with replay protection | exercised by every API test |

---

## 3. Not verified, and stated as such

| Item | Status | Why |
|---|---|---|
| Penetration test of the decision surfaces | `NOT_VERIFIED` | `EXTERNAL_DEPENDENCY` — requires an external party |
| Behaviour under a database superuser | `NOT_VERIFIED` | Out of the threat model; the owner limit in 1.5 is stated rather than mitigated |
| Rate limiting of the transition endpoint under load | `NOT_VERIFIED` | The shared limiter applies platform-wide but has not been load-tested against these routes specifically |
| Threat model document for the decision layer | `NOT_IMPLEMENTED` | Not written |
| Secret rotation exercised | `NOT_VERIFIED` | Designed, never executed — unchanged from the prior audit |
| Disaster recovery at production scale | `NOT_VERIFIED` | The exercise runs and passes here — 96 tables, 12,250 rows, 4,459 audit entries re-hashed intact, RPO 0s, RTO 1s — but against a development dataset. Production volume and topology are unproven |

---

## 4. Verdict

The decision layer does not weaken the platform's security model. Four genuine defects
were found and fixed during its construction and the alerting work that followed:

* 1.1 — a `CHECK` constraint that was inert for exactly the value it existed to catch,
  which would have allowed an invented confidence figure to be stored. That is the
  single failure the brief is most concerned with.
* 1.11 and 1.20 — absolute redirects naming an origin rather than a path, in the route
  handlers and then, missed the first time, in the middleware.
* 1.14 — an alert listing with no authorization predicate, which was harmless only for
  as long as nothing raised an alert.

Ten claims are **verified by deliberate breakage** rather than by observing a pass: the
domain predicate, the single-writer rule, the confidence constraint, the incidents
boundary, the alert-list counts, the three routing conditions, the crashed-rule
direction, and the worker actually running a pass. Each was broken on purpose, the tests
were confirmed to fail with the expected names, and the break was reverted.

Two of the guards written during this work were themselves wrong when first written and
were found only by probing them: an escalation clause that changed no outcome (and would
have muted a reopened alert), and the audit sign-in check of 1.21. A guard nobody has
seen fail is not evidence.

Nothing here is certified. The evidence engine's own refusal to certify without evidence
remains in force, and the items in section 3 are the reasons.
