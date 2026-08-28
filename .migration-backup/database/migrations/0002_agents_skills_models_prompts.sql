-- 0002 Agent, skill, model and prompt registries.
-- These are versioned controlled assets: a change produces a new version row
-- and never mutates a published one.

CREATE TABLE agents (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agent_key        text NOT NULL CHECK (agent_key ~ '^[a-z][a-z0-9_.-]{2,63}$'),
  name             text NOT NULL,
  description      text NOT NULL DEFAULT '',
  owner_user_id    uuid REFERENCES users(id),
  owner_team       text NOT NULL DEFAULT '',
  business_purpose text NOT NULL,
  risk_class       risk_class NOT NULL DEFAULT 'MEDIUM',
  max_autonomy     autonomy_level NOT NULL DEFAULT 'A2',
  status           lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  current_version  text NOT NULL DEFAULT '0.0.0',
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at       timestamptz,
  UNIQUE (tenant_id, agent_key)
);

-- Immutable published contract versions.
CREATE TABLE agent_versions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agent_id       uuid NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  version        text NOT NULL,
  contract       jsonb NOT NULL,
  contract_hash  text NOT NULL,
  status         lifecycle_status NOT NULL DEFAULT 'DRAFT',
  published_at   timestamptz,
  published_by   uuid REFERENCES users(id),
  evaluation_score numeric(5,4),
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (agent_id, version)
);
CREATE INDEX agent_versions_tenant_idx ON agent_versions(tenant_id, agent_id);

CREATE TABLE agent_contracts (
  agent_version_id  uuid PRIMARY KEY REFERENCES agent_versions(id) ON DELETE CASCADE,
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  allowed_models        text[] NOT NULL DEFAULT '{}',
  allowed_tools         text[] NOT NULL DEFAULT '{}',
  allowed_skills        text[] NOT NULL DEFAULT '{}',
  permitted_domains     text[] NOT NULL DEFAULT '{}',
  prohibited_domains    text[] NOT NULL DEFAULT '{}',
  max_classification    data_classification NOT NULL DEFAULT 'INTERNAL',
  token_budget          integer NOT NULL DEFAULT 250000 CHECK (token_budget > 0),
  cost_budget_usd       numeric(12,4) NOT NULL DEFAULT 5.0 CHECK (cost_budget_usd >= 0),
  max_runtime_seconds   integer NOT NULL DEFAULT 900 CHECK (max_runtime_seconds > 0),
  max_tool_calls        integer NOT NULL DEFAULT 50 CHECK (max_tool_calls >= 0),
  slo_success_rate      numeric(5,4) NOT NULL DEFAULT 0.95,
  slo_p95_latency_ms    integer NOT NULL DEFAULT 30000,
  requires_citations    boolean NOT NULL DEFAULT true,
  requires_provenance   boolean NOT NULL DEFAULT true,
  requires_evaluation   boolean NOT NULL DEFAULT true,
  min_evaluation_score  numeric(5,4) NOT NULL DEFAULT 0.80
);

CREATE TABLE skills (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  skill_key     text NOT NULL CHECK (skill_key ~ '^[a-z][a-z0-9_.-]{2,63}$'),
  name          text NOT NULL,
  description   text NOT NULL DEFAULT '',
  owner_team    text NOT NULL DEFAULT '',
  execution_mode text NOT NULL CHECK (execution_mode IN ('DETERMINISTIC', 'MODEL', 'HYBRID')),
  risk_class    risk_class NOT NULL DEFAULT 'LOW',
  status        lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  current_version text NOT NULL DEFAULT '1.0.0',
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, skill_key)
);

CREATE TABLE skill_versions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  skill_id       uuid NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  version        text NOT NULL,
  input_schema   jsonb NOT NULL,
  output_schema  jsonb NOT NULL,
  required_tools text[] NOT NULL DEFAULT '{}',
  evaluation_threshold numeric(5,4) NOT NULL DEFAULT 0.80,
  definition_hash text NOT NULL,
  status         lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (skill_id, version)
);

CREATE TABLE agent_skills (
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agent_id   uuid NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  skill_id   uuid NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  granted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (agent_id, skill_id)
);

-- ---------------------------------------------------------------------------
-- Model control plane
-- ---------------------------------------------------------------------------
CREATE TABLE models (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  model_key           text NOT NULL,
  provider            text NOT NULL,
  deployment          text NOT NULL DEFAULT 'cloud'
                      CHECK (deployment IN ('cloud', 'private', 'local', 'edge')),
  owner_team          text NOT NULL DEFAULT '',
  capabilities        text[] NOT NULL DEFAULT '{}',
  max_classification  data_classification NOT NULL DEFAULT 'INTERNAL',
  context_window      integer NOT NULL DEFAULT 8192,
  input_cost_per_1k   numeric(12,6) NOT NULL DEFAULT 0,
  output_cost_per_1k  numeric(12,6) NOT NULL DEFAULT 0,
  p95_latency_ms      integer NOT NULL DEFAULT 2000,
  evaluation_score    numeric(5,4),
  known_limitations   text NOT NULL DEFAULT '',
  residency           text NOT NULL DEFAULT 'global',
  approval_state      text NOT NULL DEFAULT 'PENDING'
                      CHECK (approval_state IN ('PENDING', 'APPROVED', 'REJECTED', 'SUSPENDED')),
  status              lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  effective_from      timestamptz NOT NULL DEFAULT now(),
  retirement_date     timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, model_key)
);

CREATE TABLE model_versions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  model_id     uuid NOT NULL REFERENCES models(id) ON DELETE CASCADE,
  version      text NOT NULL,
  provider_model_id text NOT NULL,
  evaluation_score numeric(5,4),
  approved_at  timestamptz,
  approved_by  uuid REFERENCES users(id),
  status       lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (model_id, version)
);

-- ---------------------------------------------------------------------------
-- Prompt registry
-- ---------------------------------------------------------------------------
CREATE TABLE prompts (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  prompt_key     text NOT NULL,
  purpose        text NOT NULL,
  owning_agent_key text NOT NULL DEFAULT '',
  status         lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  current_version text NOT NULL DEFAULT '1.0.0',
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, prompt_key)
);

CREATE TABLE prompt_versions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  prompt_id        uuid NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
  version          text NOT NULL,
  body             text NOT NULL,
  body_hash        text NOT NULL,
  author_user_id   uuid REFERENCES users(id),
  approved_by      uuid REFERENCES users(id),
  approved_at      timestamptz,
  evaluation_score numeric(5,4),
  deployment_status text NOT NULL DEFAULT 'DRAFT'
                    CHECK (deployment_status IN ('DRAFT', 'CANDIDATE', 'DEPLOYED', 'ROLLED_BACK')),
  rollback_version text NOT NULL DEFAULT '',
  effective_from   timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (prompt_id, version)
);
CREATE INDEX prompt_versions_deployed_idx
  ON prompt_versions(tenant_id, prompt_id) WHERE deployment_status = 'DEPLOYED';
