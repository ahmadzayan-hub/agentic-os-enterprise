-- 0003 Control plane: runs, plans, tasks, policies, risk, approvals, workflows.

CREATE TABLE runs (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  organization_id   uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  parent_run_id     uuid REFERENCES runs(id) ON DELETE SET NULL,
  correlation_id    text NOT NULL,
  trace_id          text NOT NULL DEFAULT '',
  idempotency_key   text,
  objective         text NOT NULL,
  intent            text NOT NULL DEFAULT '',
  requested_by      uuid REFERENCES users(id),
  owner_agent_key   text NOT NULL DEFAULT 'conductor',
  status            run_status NOT NULL DEFAULT 'PENDING',
  autonomy_level    autonomy_level NOT NULL DEFAULT 'A1',
  risk_class        risk_class NOT NULL DEFAULT 'LOW',
  risk_score        numeric(5,4) NOT NULL DEFAULT 0,
  confidence        numeric(5,4),
  classification    data_classification NOT NULL DEFAULT 'INTERNAL',
  result            jsonb,
  error_class       text,
  error_message     text,
  cost_usd          numeric(12,6) NOT NULL DEFAULT 0,
  input_tokens      bigint NOT NULL DEFAULT 0,
  output_tokens     bigint NOT NULL DEFAULT 0,
  tool_call_count   integer NOT NULL DEFAULT 0,
  duration_ms       integer,
  started_at        timestamptz,
  completed_at      timestamptz,
  deadline_at       timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX runs_tenant_created_idx ON runs(tenant_id, created_at DESC);
CREATE INDEX runs_status_idx ON runs(tenant_id, status);
CREATE UNIQUE INDEX runs_idempotency_idx
  ON runs(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE plans (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id         uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  version        integer NOT NULL DEFAULT 1,
  planner        text NOT NULL DEFAULT 'conductor.planner',
  steps          jsonb NOT NULL,
  plan_hash      text NOT NULL,
  validated      boolean NOT NULL DEFAULT false,
  validation_errors jsonb NOT NULL DEFAULT '[]'::jsonb,
  rationale      text NOT NULL DEFAULT '',
  estimated_cost_usd numeric(12,6) NOT NULL DEFAULT 0,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, version)
);

CREATE TABLE run_steps (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id            uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  step_index        integer NOT NULL,
  step_key          text NOT NULL,
  step_type         text NOT NULL CHECK (step_type IN
                     ('PLAN', 'SKILL', 'TOOL', 'MODEL', 'RETRIEVAL', 'APPROVAL',
                      'AGENT', 'WORKFLOW', 'COMPENSATION', 'VERIFICATION')),
  agent_key         text NOT NULL DEFAULT '',
  skill_key         text NOT NULL DEFAULT '',
  tool_key          text NOT NULL DEFAULT '',
  status            run_status NOT NULL DEFAULT 'PENDING',
  attempt           integer NOT NULL DEFAULT 0,
  max_attempts      integer NOT NULL DEFAULT 3,
  idempotency_key   text NOT NULL,
  input             jsonb NOT NULL DEFAULT '{}'::jsonb,
  output            jsonb,
  error_class       text,
  error_message     text,
  cost_usd          numeric(12,6) NOT NULL DEFAULT 0,
  input_tokens      integer NOT NULL DEFAULT 0,
  output_tokens     integer NOT NULL DEFAULT 0,
  latency_ms        integer,
  started_at        timestamptz,
  completed_at      timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id, step_index)
);
CREATE INDEX run_steps_run_idx ON run_steps(run_id, step_index);
CREATE UNIQUE INDEX run_steps_idempotency_idx ON run_steps(tenant_id, idempotency_key);

CREATE TABLE tasks (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id         uuid REFERENCES runs(id) ON DELETE SET NULL,
  title          text NOT NULL,
  description    text NOT NULL DEFAULT '',
  assignee_user_id uuid REFERENCES users(id),
  assignee_agent_key text NOT NULL DEFAULT '',
  status         text NOT NULL DEFAULT 'OPEN'
                 CHECK (status IN ('OPEN', 'IN_PROGRESS', 'BLOCKED', 'DONE', 'CANCELLED')),
  priority       text NOT NULL DEFAULT 'MEDIUM'
                 CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
  due_at         timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tasks_tenant_status_idx ON tasks(tenant_id, status, priority);

-- ---------------------------------------------------------------------------
-- Policy engine
-- ---------------------------------------------------------------------------
CREATE TABLE policies (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  policy_key    text NOT NULL,
  name          text NOT NULL,
  description   text NOT NULL DEFAULT '',
  category      text NOT NULL DEFAULT 'general',
  owner_team    text NOT NULL DEFAULT '',
  enforcement   text NOT NULL DEFAULT 'ENFORCE' CHECK (enforcement IN ('ENFORCE', 'MONITOR')),
  status        lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  current_version integer NOT NULL DEFAULT 1,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, policy_key)
);

CREATE TABLE policy_versions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  policy_id    uuid NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
  version      integer NOT NULL,
  rules        jsonb NOT NULL,
  rules_hash   text NOT NULL,
  effective_from timestamptz NOT NULL DEFAULT now(),
  author_user_id uuid REFERENCES users(id),
  approved_by  uuid REFERENCES users(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (policy_id, version)
);

CREATE TABLE policy_decisions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id          uuid REFERENCES runs(id) ON DELETE SET NULL,
  run_step_id     uuid REFERENCES run_steps(id) ON DELETE SET NULL,
  correlation_id  text NOT NULL,
  subject         jsonb NOT NULL,
  action          text NOT NULL,
  resource        text NOT NULL,
  effect          policy_effect NOT NULL,
  matched_policies jsonb NOT NULL DEFAULT '[]'::jsonb,
  obligations     jsonb NOT NULL DEFAULT '[]'::jsonb,
  reason          text NOT NULL DEFAULT '',
  enforcement     text NOT NULL DEFAULT 'ENFORCE',
  evaluated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX policy_decisions_run_idx ON policy_decisions(run_id);
CREATE INDEX policy_decisions_tenant_time_idx ON policy_decisions(tenant_id, evaluated_at DESC);

CREATE TABLE risk_assessments (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id         uuid REFERENCES runs(id) ON DELETE CASCADE,
  run_step_id    uuid REFERENCES run_steps(id) ON DELETE CASCADE,
  action         text NOT NULL,
  risk_class     risk_class NOT NULL,
  risk_score     numeric(5,4) NOT NULL,
  factors        jsonb NOT NULL DEFAULT '[]'::jsonb,
  reversibility  text NOT NULL DEFAULT 'REVERSIBLE'
                 CHECK (reversibility IN ('REVERSIBLE', 'PARTIAL', 'IRREVERSIBLE')),
  financial_impact_usd numeric(14,2) NOT NULL DEFAULT 0,
  required_autonomy autonomy_level NOT NULL DEFAULT 'A1',
  assessed_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX risk_assessments_run_idx ON risk_assessments(run_id);

-- ---------------------------------------------------------------------------
-- Approval engine
-- ---------------------------------------------------------------------------
CREATE TABLE approvals (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id            uuid REFERENCES runs(id) ON DELETE CASCADE,
  run_step_id       uuid REFERENCES run_steps(id) ON DELETE CASCADE,
  requested_by_agent text NOT NULL DEFAULT '',
  mode              text NOT NULL DEFAULT 'SINGLE'
                    CHECK (mode IN ('SINGLE', 'DUAL', 'SEQUENTIAL', 'PARALLEL')),
  required_approvals integer NOT NULL DEFAULT 1 CHECK (required_approvals >= 1),
  status            approval_status NOT NULL DEFAULT 'PENDING',
  action            text NOT NULL,
  target            text NOT NULL DEFAULT '',
  autonomy_level    autonomy_level NOT NULL DEFAULT 'A4',
  risk_class        risk_class NOT NULL DEFAULT 'HIGH',
  financial_impact_usd numeric(14,2) NOT NULL DEFAULT 0,
  reversibility     text NOT NULL DEFAULT 'IRREVERSIBLE',
  confidence        numeric(5,4),
  reason            text NOT NULL DEFAULT '',
  evidence          jsonb NOT NULL DEFAULT '[]'::jsonb,
  sources           jsonb NOT NULL DEFAULT '[]'::jsonb,
  consequences      text NOT NULL DEFAULT '',
  policy_refs       jsonb NOT NULL DEFAULT '[]'::jsonb,
  approve_and_execute boolean NOT NULL DEFAULT false,
  expires_at        timestamptz NOT NULL,
  escalate_to_role  text NOT NULL DEFAULT '',
  decided_at        timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX approvals_pending_idx ON approvals(tenant_id, status, expires_at);

CREATE TABLE approval_steps (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  approval_id   uuid NOT NULL REFERENCES approvals(id) ON DELETE CASCADE,
  sequence      integer NOT NULL DEFAULT 1,
  approver_user_id uuid REFERENCES users(id),
  approver_role  text NOT NULL DEFAULT '',
  delegated_from uuid REFERENCES users(id),
  decision      approval_status NOT NULL DEFAULT 'PENDING',
  comment       text NOT NULL DEFAULT '',
  decided_at    timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX approval_steps_approval_idx ON approval_steps(approval_id, sequence);

-- ---------------------------------------------------------------------------
-- Workflow engine
-- ---------------------------------------------------------------------------
CREATE TABLE workflows (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  workflow_key  text NOT NULL,
  name          text NOT NULL,
  description   text NOT NULL DEFAULT '',
  owner_team    text NOT NULL DEFAULT '',
  status        lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  current_version integer NOT NULL DEFAULT 1,
  max_concurrent_runs integer NOT NULL DEFAULT 10 CHECK (max_concurrent_runs > 0),
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, workflow_key)
);

CREATE TABLE workflow_versions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  workflow_id   uuid NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  version       integer NOT NULL,
  definition    jsonb NOT NULL,
  definition_hash text NOT NULL,
  status        lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workflow_id, version)
);

CREATE TABLE workflow_runs (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  workflow_id      uuid NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
  workflow_version integer NOT NULL,
  run_id           uuid REFERENCES runs(id) ON DELETE SET NULL,
  parent_workflow_run_id uuid REFERENCES workflow_runs(id) ON DELETE SET NULL,
  correlation_id   text NOT NULL,
  idempotency_key  text,
  status           run_status NOT NULL DEFAULT 'PENDING',
  current_step     integer NOT NULL DEFAULT 0,
  state            jsonb NOT NULL DEFAULT '{}'::jsonb,
  input            jsonb NOT NULL DEFAULT '{}'::jsonb,
  output           jsonb,
  error_class      text,
  error_message    text,
  paused           boolean NOT NULL DEFAULT false,
  cancel_requested boolean NOT NULL DEFAULT false,
  next_poll_at     timestamptz NOT NULL DEFAULT now(),
  lease_owner      text,
  lease_expires_at timestamptz,
  deadline_at      timestamptz,
  started_at       timestamptz,
  completed_at     timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX workflow_runs_poll_idx ON workflow_runs(status, next_poll_at)
  WHERE status IN ('PENDING', 'RUNNING');
CREATE UNIQUE INDEX workflow_runs_idempotency_idx
  ON workflow_runs(tenant_id, workflow_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE workflow_steps (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  workflow_run_id  uuid NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
  step_index       integer NOT NULL,
  step_key         text NOT NULL,
  step_type        text NOT NULL,
  status           run_status NOT NULL DEFAULT 'PENDING',
  attempt          integer NOT NULL DEFAULT 0,
  max_attempts     integer NOT NULL DEFAULT 3,
  backoff_seconds  integer NOT NULL DEFAULT 2,
  timeout_seconds  integer NOT NULL DEFAULT 120,
  idempotency_key  text NOT NULL,
  input            jsonb NOT NULL DEFAULT '{}'::jsonb,
  output           jsonb,
  error_class      text,
  error_message    text,
  compensation_for integer,
  compensated      boolean NOT NULL DEFAULT false,
  scheduled_at     timestamptz NOT NULL DEFAULT now(),
  started_at       timestamptz,
  completed_at     timestamptz,
  UNIQUE (workflow_run_id, step_index)
);
CREATE UNIQUE INDEX workflow_steps_idem_idx ON workflow_steps(tenant_id, idempotency_key);

CREATE TABLE workflow_dead_letters (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  workflow_run_id uuid REFERENCES workflow_runs(id) ON DELETE SET NULL,
  workflow_step_id uuid REFERENCES workflow_steps(id) ON DELETE SET NULL,
  reason          text NOT NULL,
  error_class     text NOT NULL,
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  attempts        integer NOT NULL DEFAULT 0,
  resolved_at     timestamptz,
  resolved_by     uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE schedules (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  schedule_key  text NOT NULL,
  workflow_key  text NOT NULL,
  cron          text NOT NULL,
  timezone      text NOT NULL DEFAULT 'UTC',
  input         jsonb NOT NULL DEFAULT '{}'::jsonb,
  enabled       boolean NOT NULL DEFAULT true,
  last_run_at   timestamptz,
  next_run_at   timestamptz NOT NULL DEFAULT now(),
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, schedule_key)
);
