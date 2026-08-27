# Developer Guide

## Run it

The fastest path is Compose — it brings up PostgreSQL with pgvector, Redis, the
API, the worker and the console, applies migrations and seeds two tenants:

```bash
docker compose up --build
```

Then open http://localhost:3000 and sign in as `systems.lead@rta.example` with
the password the `migrate` service prints. The API is on
http://localhost:8000, its OpenAPI document at
http://localhost:8000/api/v1/docs.

### Without Compose

You need PostgreSQL 16 with `pgvector`, `pgcrypto` and `pg_trgm`, and a
superuser to run the cluster bootstrap once:

```bash
psql -U postgres -f database/bootstrap/00_cluster_bootstrap.sql
psql -U postgres -d agentic -f database/bootstrap/01_extensions.sql

python -m venv .venv && .venv/bin/pip install -e ".[dev,parsers]"
cp .env.example .env          # then fill in AGENTIC_JWT_SECRET
agentic-migrate && agentic-seed
uvicorn agentic_os.api.app:app --reload      # API
agentic-worker                                # background worker
cd apps/web && npm install && npm run dev     # console
```

`scripts/dev_db_reset.sh` drops and recreates the development database.

## Layout

```
packages/agentic_os/src/agentic_os/
  core/         config, ids, context, crypto, db, migrations, registries, seed
  identity/     permissions, authentication, authorization, MFA, provisioning
  control/      intent router, planner, policy, risk, approval, conductor
  runtime/      skills, event bus, workflow engine, agent runtime, step types
  ai/           context firewall, providers, model gateway, prompt registry
  tools/        tool security gateway, built-in tools, MCP, secret broker
  knowledge/    embeddings, PII, chunking, parsers, ingestion, retrieval, graph
  privacy/      data subject requests and retention
  resilience/   backup and restore exercise
  assurance/    audit ledger, evidence engine, CLI
  observability/ telemetry
  outcomes/     business outcome and ROI engine
  worker/       the background loop
  api/          FastAPI application and v1 routers
apps/web/       Next.js 15 operator console
database/       bootstrap SQL and forward-only migrations
packages/contracts, skills, models, tools, policies, prompts, workflows
                declarative registries — the platform's configuration surface
evaluations/    the assurance control catalogue
tests/          unit, integration, database, tenant isolation, api, agents,
                rag, security, redteam, workflows, accessibility, evals
infrastructure/ docker, kubernetes, terraform
```

## Conventions that are not negotiable

**No ORM models.** Every query is SQLAlchemy Core `text()` with bound
parameters. This keeps the SQL — and therefore the row level security
predicates and the ACL joins — visible at the call site. Where a query
interpolates an identifier, the interpolated value is a module-level constant,
never caller input.

**Tenant binding, not `WHERE tenant_id`.** Use `session_scope(ctx)` or
`bind_tenant(session, tenant_id)`. RLS does the filtering. The GUC is
transaction-local, so it is cleared by a commit or rollback — re-bind before
reading again. Forgetting to bind returns zero rows rather than leaking.

**The conductor never executes a tool.** It plans, validates and dispatches.
Execution happens in the agent runtime through the tool security gateway.
(Architecture Constitution rule 17, enforced by `tests/security/test_tool_gateway.py`.)

**Every side effect passes the gateway.** Identity → authorization → risk →
policy → approval where required → execution → verification → audit → evidence.
There is no second path.

**Secrets never reach a prompt.** The secret broker resolves a reference at
call time inside the tool gateway; the model never sees the value, and
`redact_payload` scrubs anything that looks like one before it reaches the
ledger.

**Registries are the configuration surface.** Adding an agent, skill, model,
tool, policy or prompt means editing YAML under the corresponding directory and
running `agentic-seed`. `validate_registries()` runs in CI and fails on an
inconsistent catalogue.

## Running a real model

The platform answers without any model provider: routing falls back to the
deterministic executor and records the substitution on the run. Nothing is
faked — a deterministic answer is labelled as one.

To run an actual open-weights model locally, no API key and no third party:

```bash
ollama pull qwen2.5:7b-instruct
export AGENTIC_MODEL_ENDPOINTS='{"onprem-ollama":{"base_url":"http://127.0.0.1:11434/v1","external":false}}'
```

`private-fast` is now routable. `AGENTIC_MODEL_ALLOW_EXTERNAL_PROVIDERS` is not
needed, because a privately operated endpoint is not an external provider.

### Endpoints

`AGENTIC_MODEL_ENDPOINTS` is a JSON map of named endpoints, and each entry in
`models/registry.yaml` selects one with its `endpoint:` field. That indirection
is why a self-hosted deployment and a third-party host can be registered at the
same time; with a single base URL in settings, the second model registered
would silently talk to the first one's server.

| Field | Meaning |
|---|---|
| `base_url` | OpenAI-compatible root. Ollama, vLLM, TGI and llama.cpp all serve it under `/v1` — omitting the suffix 404s every call. |
| `api_key_ref` | A *reference* the secret broker resolves at call time. Never a key. Omit it for a private endpoint that needs none. |
| `external` | `false` marks the endpoint operator-controlled: reachable without the external-provider switch, and eligible for RESTRICTED work. |

An endpoint a model names but which is not configured makes that model
*unavailable*, so the gateway routes on. It never falls through to a different
endpoint — that would send data to a server the registry entry never named.

### What may see what

The registry validator enforces two rules that are easy to violate by accident:

* A model cleared above `INTERNAL` must declare `allows_training_on_input:
  false`. The build brief forbids RTA data reaching external training without
  explicit approval, and the registry entry is where that claim lives.
* A model cleared for `RESTRICTED` must be `local` or `private`. Open weights
  are not the point — *where the inference runs* is. `oss-cloud-fast` runs the
  same Llama 3.3 weights as `private-general` and is capped at `CONFIDENTIAL`,
  because the prompt leaves the residency boundary.

Both are declarations, not enforcement at the provider. They make the claim
reviewable and refuse the contradictory combinations.

## Adding things

**A skill.** Implement it in `runtime/skills.py`, register it in
`skills/registry.yaml` with its input and output schema, and add it to the
`allowed_skills` of any agent contract that should use it. Skills are
deterministic where the task allows; a skill that needs generation calls the
model gateway rather than a provider directly.

**A tool.** Implement it in `tools/builtin.py` and declare it in
`tools/registry.yaml` with `side_effect`, `reversibility`, `risk_class`,
`min_autonomy`, `max_classification`, `scopes` and `requires_approval`. Those
fields are the gateway's inputs, not documentation. A declared tool with
`implementation_status: NOT_IMPLEMENTED` is refused with a typed error and
shown as such in the console — that is the supported way to declare intent
without shipping a fake.

**A migration.** Add `database/migrations/NNNN_description.sql`. Migrations are
forward-only and checksummed; editing an applied one is rejected
(`tests/database/test_migrations.py`). New tables carrying `tenant_id` get RLS
automatically from the loop in `0008`; check that grants are right.

**A control.** Add it to `evaluations/controls.yaml` with a weight and a `test:`
reference to a test that actually proves it. A control without a test is
NOT_EVIDENCED and scores zero — which is the correct way to record something
the platform does not yet do.

## Tests

```bash
pytest                          # everything (needs the database)
pytest -m unit                  # no database required
pytest tests/tenant_isolation   # the isolation proofs
agentic-evidence collect        # run the suite and derive maturity
```

Integration tests run against a real PostgreSQL. There is no in-memory
substitute, because row level security, the ledger triggers and the vector
index are the things under test and a fake would not have them.

### Skips, and when they are not allowed

Tests that need a real service carry a gate: `requires_db`, `requires_redis`,
`requires_dr_identity` in `tests/conftest.py`. By default an absent service
skips those tests, so you can work without a local Redis.

That default is wrong for CI, where every service is provisioned and its
absence is a defect. A skip and a pass are the same exit code, so a database
that died mid-job would skip every integration test and still report green.
Set `AGENTIC_REQUIRE_SERVICES` to make absence fail instead:

```bash
AGENTIC_REQUIRE_SERVICES=db,redis pytest    # what CI runs
AGENTIC_REQUIRE_SERVICES=all pytest         # including the DR identity
```

A name that is not a known service fails the run at start-up rather than being
ignored — a typo in CI must not quietly mean "require nothing". CI sets the
variable to exactly the services its test job provisions, and
`tests/api/test_repository_hygiene.py::test_the_test_job_cannot_pass_by_skipping_its_services`
fails if a service is ever added without being required.

The accessibility suite consumes a report produced by a real browser:

```bash
cd apps/web && npm run build && node .next/standalone/server.js &
node tests/accessibility/axe_audit.mjs --base http://127.0.0.1:3000 \
  --out ../../artifacts/accessibility.json
pytest tests/accessibility
```

### Language and direction

The console reads its locale from the `agentic_locale` cookie and sets
`<html lang>` and `<html dir>` from it on the server. To see a surface in
Arabic:

```bash
curl -b 'agentic_locale=ar' http://127.0.0.1:3000/runs | grep -o '<html[^>]*>'
# <html lang="ar" dir="rtl">
```

Two rules keep it working, both enforced by `pytest tests/i18n`:

* **No stylesheet rule may name a physical side.** `margin-left`, `text-align:
  right`, a bare `left:` offset and `float` all break when the page mirrors.
  Use `margin-inline-start`, `text-align: start`, `inset-inline-start`, and
  flex or grid. The test reads declarations wherever they sit on a line, so a
  one-line rule is caught too.
* **A message key must exist in every locale.** English is the source of
  truth; adding a key without its Arabic translation fails the build rather
  than rendering English inside an Arabic page.

The accessibility audit runs the full surface set in both directions and
asserts the rendered `dir` matches the locale it asked for, so an RTL pass
cannot report zero violations because it silently ran in English.

## Quality gates

```bash
scripts/preflight.sh --staged    # before committing
scripts/preflight.sh             # before pushing
```

That runs what CI runs, in CI's order: ruff lint, ruff format, the secret scan,
bandit, and the suite under a bare `pytest`. A tool you have not installed is
reported as *skipped* rather than passing, because a skipped check that looks
like a green one is how things reach CI broken.

Two details in there are load-bearing, both learned the hard way:

* **`gitleaks detect` scans committed history, not your working tree.** Running
  it before you commit checks everything except the change you just made. The
  script runs `gitleaks protect --staged` as well, which sees the diff you are
  about to commit. A secret-shaped literal in a new test slipped past a local
  scan twice before this existed.
* **The suite runs under a bare `pytest`, not `python -m pytest`.** The latter
  adds the working directory to `sys.path` and will hide an import error that
  CI hits immediately.

`mypy packages/agentic_os/src/agentic_os` is advisory and not in the script;
annotation debt remains.
