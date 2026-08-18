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

The accessibility suite consumes a report produced by a real browser:

```bash
cd apps/web && npm run build && node .next/standalone/server.js &
node tests/accessibility/axe_audit.mjs --base http://127.0.0.1:3000 \
  --out ../../artifacts/accessibility.json
pytest tests/accessibility
```

## Quality gates

```bash
ruff check . && ruff format --check .
mypy packages/agentic_os/src/agentic_os     # advisory; annotation debt remains
```

Both run in CI. Keep them green — CI runs `ruff format --check .` across the
whole repository, so an unformatted file fails the build.
