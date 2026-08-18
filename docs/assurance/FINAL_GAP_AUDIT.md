# Final Gap Audit — Agentic OS Enterprise v3.1

An audit of this build against what a production deployment at an authority
like RTA would actually require. It is written to be used against the platform,
not to defend it. Nothing found here is omitted, and where a gap is also a
scored control the control identifier is given.

**Summary: the governed execution path is real and proven by executable tests.
The platform has never been deployed, never been independently assessed and
never been run in production. Those three facts are the substance of the
5-point shortfall in `MATURITY_REPORT.md`, and they are the honest headline.**

Method: every claim below was checked against the repository and, where it was
testable, against a run. Claims that could not be checked are marked as such
rather than assumed.

---

## 1. Blocking gaps — must close before a production decision

### 1.1 Never deployed (DEP-003, weight 2, NOT_EVIDENCED)
The manifests, images and pipeline now build and validate in CI. What has never
happened is an **apply to a cluster**, and that is what this control requires.

Verified in CI, on this branch:

* The container image **builds**, and Trivy scans it. This closed a defect the
  build environment could not have found — the image never built at all,
  because the Dockerfile copied a directory git had never tracked.
* All four kustomize overlays **render** (`kubectl kustomize`).
* Terraform **fmt, init and validate** pass for all four environments. This
  closed a second real defect: the environment roots implicitly required
  `hashicorp/postgresql`, a provider that does not exist, so `terraform init`
  would have failed in any real environment.
* `docker compose config` passes.

Still unverified, and the reason this control is NOT_EVIDENCED:

* **No apply.** No `kubectl apply`, no `terraform apply`, no cluster of any
  kind. `terraform validate` checks that the configuration is coherent; it
  does not contact a provider or create anything. The `postgresql` provider's
  behaviour against a managed instance, the resource attribute semantics, the
  readiness of the workloads under a real scheduler, and the PGDG step that
  installs `postgresql-client-16` for the DR exercise are all untested by a
  build that never ran the image or the plan.

The pipeline that proves the above is itself worth recording, because running
it changed what this repository can honestly claim. Its first five runs found,
in order: every integration test failing to collect; a Trojan Source hazard in
the context firewall; an image build broken by an untracked directory; a
Terraform provider that does not exist; two secret-scan findings that three
earlier failures had masked; a CI step asserting that an implemented control
was unimplemented; and an evidence engine hardcoded to the author's own
virtualenv, so it could never have run anywhere else. Every one of these was
invisible to a local test run. None was suppressed.

### 1.2 No independent assessment (IND-001, weight 2, NOT_EVIDENCED)
No penetration test, no external code review, no third-party control
assessment. The red-team suite in `tests/redteam/` is written by the same
author as the controls it probes; it is a useful regression net and is not
independent assurance. Until a second party has tried to break this, the
security claims are self-reported.

### 1.3 No production operation (IND-002, weight 1, NOT_EVIDENCED)
Every passing control is VERIFIED in a development environment. Not one is
PRODUCTION_PROVEN. Nothing here has met real users, real load, real data
volumes or a real incident.

### 1.4 Load behaviour measured; capacity still not established (PRF-003, weight 1, NOT_EVIDENCED)
Was: no load test at all, so any capacity statement would have been invention.
There is now a concurrency sweep (`scripts/loadtest.py`) run by CI, held to
account by `tests/performance`, with a missing report failing rather than
skipping.

What it found is worth more than the numbers: **throughput peaks near
concurrency 8 and falls at 32, while latency roughly quadruples.** Past that
point the platform queues rather than doing more work. Every request succeeded
at every level — no connection-pool exhaustion, no tenant binding lost under
load — but a deployment sized from the uncontended numbers would be sizing from
the wrong end of the curve. The manifests' two replicas and the pool settings
(10 + 20 overflow) should be revisited against this shape before any capacity
commitment.

Still unmeasured, and why the control stays NOT_EVIDENCED: one API process, one
database, one shared development host, a seeded corpus. No production-scale
data volume, no representative hardware, no headroom margin, no sustained soak,
and **no measurement of the worker or of end-to-end governed runs** — the sweep
covers read paths and the API surface, not a full agent execution under load.

---

## 2. Significant gaps — real limitations of the current build

### 2.1 The AI plane runs deterministically by default
The model gateway, routing rules, classification ceilings, prompt registry and
context firewall are real and tested. What they route to, by default, is a
deterministic local provider (extractive summarisation, lexical
classification, hashed-n-gram embeddings). External provider adapters exist but
**no test calls a vendor API**, and no vendor model's output quality is
evidenced by anything in this repository. The governance of model calls is
verified; the models are not.

### 2.2 Seven of sixteen tools are NOT_IMPLEMENTED
They are declared in `tools/registry.yaml` with `implementation_status:
NOT_IMPLEMENTED`, refused at the gateway with a typed error, and shown as
NOT_IMPLEMENTED on the Capabilities surface. This is deliberate and visible
rather than hidden — but it means the tool surface is nine working tools, and
the seven include the ones that would touch real operational systems.

### 2.3 No real MCP server has ever been connected
MCP authorisation, schema-hash pinning and the constraint that forbids
untrusted token forwarding are implemented and tested against fixtures. No
external MCP server has been registered, approved or invoked. The interop
claim is **mapped and unit-tested, not integration-proven**.

### 2.4 Document parsing accuracy is reported, not measured
Parsers report a confidence score and list unsupported elements, and refuse to
claim completeness — which is the correct behaviour. But there is no labelled
corpus, so parser accuracy against real RTA documents (scanned PDFs, complex
tables, Arabic text) is **unknown**. No accuracy percentage should be quoted
from this build.

### 2.5 Retrieval quality is evaluated on a small synthetic set
The RAG evaluation suite measures recall, precision and citation-groundedness
against a seeded corpus written for this build. It proves the pipeline behaves
correctly, including the permission-aware retrieval property (authorisation
occurs inside the SQL, not after it). It does **not** establish retrieval
quality on the customer's real corpus.

### 2.6 Disaster recovery is proven at development scale only
DRP-001 is genuinely evidenced: a real `pg_dump`, restored into a real scratch
database, every table's row count compared, every tenant's audit hash chain
recomputed inside the restored copy, RPO and RTO measured, scratch database
dropped. The measured RTO of ~1 second is a measurement of a 0.6 MB database
on the same host. It is evidence that the *procedure* works; it is not a
production RTO. Also absent: WAL archiving and point-in-time recovery,
cross-region replication, failover drills, and encryption of the dump artefact
at rest (the exercise writes an unencrypted dump to a volume).

One more thing about this control's provenance: the exercise needs a break-glass
maintenance identity, and CI has none. So DRP-001 is evidenced from local runs,
not from CI, and CI deliberately does not require the `dr` service gate — see
section 3 below. Anyone reading the score should know that this control's evidence
comes from a developer's machine rather than from the pipeline.

### 2.7 Identity federation is not implemented
Local password (Argon2id) plus RFC 6238 TOTP with replay protection is
implemented and tested. There is no OIDC or SAML federation, no SCIM
provisioning, and no WebAuthn — the `identity_provider` column exists and only
`local` is wired. An enterprise deployment would require federation on day one.

### 2.8 Rate limiting — resolved, with one caveat
Was: an in-process counter, so behind two replicas the effective limit doubled
and it reset on restart. Now a Redis sliding window evaluated in a single Lua
script, so the check and the increment cannot interleave between replicas —
the race that makes a naive GET/INCR limiter leak requests under concurrency.
Proven against a real Redis in CI: two limiter instances sharing a namespace
refuse the fifth request of a limit-4 window whichever one receives it.

Two deliberate properties worth review rather than assumption:

* **Degradation, not failure.** If Redis is unreachable the limiter falls back
  to its in-process twin. Failing closed would turn a cache outage into a total
  outage; failing open would remove the control. The fallback keeps a real
  bound — per replica instead of global — and logs the transition once.
* **Keys are hashed.** The limiter's key is derived from an Authorization
  header, and a shared store is a wider blast radius than process memory, so
  what reaches Redis is a SHA-256 digest. A test asserts no token material
  appears in any Redis key.

Remaining caveat: the limiter bounds requests per principal per minute. It is
not a defence against a distributed flood, which belongs at the ingress.

### 2.9 Secret and KMS backends are partly unexercised
The `env` secret backend and the local KMS envelope are exercised by tests.
The `file` and `vault` backends and the `aws-kms` / `azure-kv` / `gcp-kms`
paths are code that has never run against the real service. Production
configuration selects exactly those unexercised paths.

### 2.10 Data residency is recorded, not enforced
Tenants carry `region` and `data_residency` values that are stored and
displayed. Nothing in the platform prevents data for a tenant marked one
residency from being processed by infrastructure in another; that enforcement
lives in deployment topology, which does not exist yet (1.1).

### 2.11 Accessibility is automated-only
23 surfaces × 2 colour schemes, 0 axe violations, 0 serious or critical — run
against the real application in a real browser, and now in CI as well, against
a seeded database with the API and console actually serving. But axe detects a minority of
WCAG failures. There has been **no screen-reader pass, no keyboard-only
walkthrough by a person, no testing with users**, and only Chromium was
exercised. WCAG 2.2 AA conformance is therefore *not* claimed; what is claimed
is that the automated pass is clean.

### 2.12 No internationalisation
The console is English-only, `lang="en"`, with no RTL support. For an Arabic-
first authority this is a material product gap, not a technical detail.

### 2.13 Cost figures are computed, not reconciled
Cost records are derived from configured unit prices in the model registry.
They have never been reconciled against a provider invoice, so they are an
internal accounting estimate. ROI in the outcomes engine correctly excludes
ESTIMATED outcomes and counts only MEASURED ones — but in a fresh install the
measured baselines are seeded values, so no ROI number from this build is a
business claim.

---

## 3. Smaller gaps and known debt

* **mypy is advisory in CI** (`continue-on-error: true`). Type-annotation debt
  remains; type errors do not fail the build.
* **Outbox handlers are in-process.** The event bus is a durable transactional
  outbox with retries and a dead-letter queue, but every handler runs inside
  the worker. There is no external subscriber transport.
* **Compose ships development placeholder credentials.** They are obvious and
  documented, the database is not published to the host, and `.env` is
  git-ignored — but a careless `docker compose up` in a shared environment is
  still a weak-credential deployment.
* **The DR maintenance identity needs high privilege.** `CREATE EXTENSION
  vector` requires superuser because pgvector is not a trusted extension, so
  the restore identity is effectively a superuser. It is isolated to one
  CronJob and one secret, and that isolation is asserted by a test, but it is
  the largest standing privilege in the design.
* **FORCE row level security means backups require that identity.** The owner
  role's own `COPY` is filtered by the tenant policy, so `pg_dump` fails as the
  owner. This is a consequence of having no bypass predicate — the isolation
  model working as intended — but it must be understood by whoever runs
  backups.
* **Screenshots and the accessibility report are point-in-time artefacts.**
  They are regenerated by a command, not by CI, so they can drift from the
  code between releases.
* **A skipped test used to read as a passing one.** `requires_db` and
  `requires_redis` turned an unreachable service into a skip, so a CI job whose
  database died partway through would have skipped every integration test and
  still exited zero. `AGENTIC_REQUIRE_SERVICES` now names the services whose
  absence must fail the run and CI sets it to the services it provisions
  (EVL-004). The residual: `dr` is intentionally not required, because CI has no
  maintenance identity — so DRP-001's evidence still comes from local runs
  only, and nothing in the pipeline would notice if that exercise stopped
  working. Closing it needs a CI-side maintenance credential, which is a
  deployment decision rather than a code change.
* **Two seeded tenants, ten users.** Tenant isolation is proven empirically
  between two tenants. Behaviour at hundreds of tenants — connection pooling
  per tenant, the worker's per-tenant sweep, index selectivity — is untested.

---

## 4. What was checked and found sound

Listed so the audit is balanced, and because each is reproducible.

* **The governed path holds end to end.** Authenticate → intent → plan →
  contract validation → risk → policy → approval where required → dispatch →
  agent runtime → skill → tool gateway → ACL-aware retrieval → cited answer →
  hash-chained audit. A consequential objective is blocked by the validator; a
  reversible one proceeds under an explicit policy rule with a verification
  obligation.
* **Tenant isolation has no bypass.** Every tenant table has FORCE row level
  security and a policy with no escape predicate. The one BYPASSRLS role is
  NOLOGIN and reachable only through `SET ROLE` inside SECURITY DEFINER
  functions. An unbound session sees nothing; a bound session sees one tenant.
* **Authorisation precedes retrieval.** The ACL predicate and the clearance
  comparison are inside the retrieval SQL. A RESTRICTED document is invisible
  to a lower clearance and to a principal without the granted role — it is not
  fetched and then filtered.
* **The audit ledger is append-only and tamper-evident.** Triggers refuse
  UPDATE, DELETE and TRUNCATE for every role including the owner; the chain is
  recomputed in the database by `audit_verify_chain`, and it verified intact
  inside a restored copy.
* **The conductor never executes production tools.** Constitution rule 17 is
  enforced structurally, not by convention.
* **Maturity cannot be asserted by hand.** It is derived from a JUnit report by
  one function, and a critical control failure blocks certification whatever
  the score.
* **Nothing in the console is decorative.** Every surface renders live API
  data; unimplemented capabilities appear as NOT_IMPLEMENTED on the
  Capabilities surface rather than as working-looking controls; navigation is
  filtered by the principal's permissions so no link exists only to deny.
* **No secret is committed.** `.env.example` ships every credential key blank,
  a test enforces it, and gitleaks runs in CI.

---

## 5. Recommended order of work

1. Build the images and apply to a dev cluster; close DEP-003 with a recorded
   apply and a passing readiness check. This also validates the Dockerfiles,
   the Terraform and the pipeline in one pass.
2. Commission an independent security assessment; close IND-001.
3. Add OIDC federation — with the shared rate limiter now in place, this is
   the remaining gap that blocks a real enterprise pilot regardless of anything
   else.
4. Re-run the load test on production-representative hardware, extend it to
   the worker and to end-to-end governed runs, and close PRF-003.
5. Exercise DR at production scale with WAL archiving and point-in-time
   recovery, and encrypt the dump artefact.
6. Manual accessibility and screen-reader audit; add Arabic and RTL.
7. Only then consider a production certification claim — and only from a
   collection run against production, which is what would make IND-002
   evidenceable.

---

## 6. Process note

The build brief asked for the work to be done on
`build/agentic-os-enterprise-v3.1`. The execution harness for this session
designated `claude/agentic-os-enterprise-v3.1-ogi9cq` instead, so development
happened there; with the maintainer's approval the identical commits are also
published as `build/agentic-os-enterprise-v3.1`, and the pull request is opened
from that branch. Both refs point at the same commit.

`main` did not exist on the remote when this build finished — the repository
held no branches at all, so there was no base for a pull request. With the
maintainer's approval, `main` was created at the unmodified v3.0 scaffold
commit (`294dfca`), which is already an ancestor of this branch. `main` was
created, not modified: its content is exactly the baseline the v3.0 scaffold
defined, and every v3.1 change is in the pull request rather than on `main`.
