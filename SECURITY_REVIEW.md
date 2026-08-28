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

### 1.11 MFA replay protection is real, and the tests do not defeat it — **verified**

Signing in twice as an MFA-required user inside one 30-second window genuinely fails.
The suite caches tokens per run and, when an earlier module has spent the current code,
waits for the next period. Disabling the control to make the suite convenient would have
left it green with the control off.

---

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
| Disaster recovery for the new tables | `NOT_VERIFIED` | `PRODUCTION_CONFIGURATION_REQUIRED` — `AGENTIC_DR_ADMIN_URL` is unset, and the exercise refuses to fabricate evidence |

---

## 4. Verdict

The decision layer does not weaken the platform's security model, and two genuine
defects were found and fixed during its construction — one of which (1.1) would have
allowed an invented confidence figure to be stored, which is the single failure the brief
is most concerned with.

Three claims are **verified by deliberate breakage** rather than by observing a pass:
the domain predicate, the single-writer rule, and the confidence constraint. Each was
broken on purpose, the tests were confirmed to fail, and the break was reverted.

Nothing here is certified. The evidence engine's own refusal to certify without evidence
remains in force, and the items in section 3 are the reasons.
