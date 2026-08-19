-- 0006 Events, audit ledger, assurance and observability tables.

CREATE TABLE events (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  event_type      text NOT NULL,
  event_version   integer NOT NULL DEFAULT 1,
  aggregate_type  text NOT NULL DEFAULT '',
  aggregate_id    text NOT NULL DEFAULT '',
  correlation_id  text NOT NULL DEFAULT '',
  causation_id    text NOT NULL DEFAULT '',
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX events_tenant_type_idx ON events(tenant_id, event_type, occurred_at DESC);
CREATE INDEX events_aggregate_idx ON events(tenant_id, aggregate_type, aggregate_id);

-- Transactional outbox: written in the same transaction as the state change,
-- dispatched asynchronously with at-least-once delivery.
CREATE TABLE outbox_events (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  event_id        uuid REFERENCES events(id) ON DELETE SET NULL,
  event_type      text NOT NULL,
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  correlation_id  text NOT NULL DEFAULT '',
  status          text NOT NULL DEFAULT 'PENDING'
                  CHECK (status IN ('PENDING', 'DISPATCHING', 'DISPATCHED', 'FAILED', 'DEAD')),
  attempts        integer NOT NULL DEFAULT 0,
  max_attempts    integer NOT NULL DEFAULT 8,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  last_error      text NOT NULL DEFAULT '',
  dispatched_at   timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX outbox_pending_idx ON outbox_events(status, next_attempt_at)
  WHERE status IN ('PENDING', 'FAILED');

CREATE TABLE event_subscriptions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  subscription_key text NOT NULL,
  event_pattern  text NOT NULL,
  target_type    text NOT NULL CHECK (target_type IN ('WORKFLOW', 'AGENT', 'WEBHOOK')),
  target_key     text NOT NULL,
  enabled        boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, subscription_key)
);

-- ---------------------------------------------------------------------------
-- Append-only, hash-chained audit ledger
-- ---------------------------------------------------------------------------
CREATE TABLE audit_events (
  id               bigserial PRIMARY KEY,
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
  sequence_no      bigint NOT NULL,
  category         text NOT NULL CHECK (category IN
                   ('AUTH', 'AUTHZ', 'USER_ACTION', 'AGENT_ACTION', 'APPROVAL',
                    'POLICY', 'MODEL_CALL', 'TOOL_CALL', 'DATA_ACCESS', 'SECURITY',
                    'CONFIG_CHANGE', 'PROMPT_CHANGE', 'WORKFLOW_CHANGE',
                    'PRIVILEGE_CHANGE', 'KILL_SWITCH', 'EVIDENCE')),
  action           text NOT NULL,
  outcome          text NOT NULL CHECK (outcome IN ('SUCCESS', 'DENIED', 'FAILURE')),
  human_id         uuid,
  agent_id         text,
  agent_version    text,
  workflow_run_id  uuid,
  tool_id          text,
  service_principal text,
  resource_type    text NOT NULL DEFAULT '',
  resource_id      text NOT NULL DEFAULT '',
  correlation_id   text NOT NULL DEFAULT '',
  run_id           uuid,
  classification   data_classification NOT NULL DEFAULT 'INTERNAL',
  payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
  previous_hash    text NOT NULL,
  entry_hash       text NOT NULL,
  signature        text NOT NULL DEFAULT '',
  occurred_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX audit_events_seq_idx ON audit_events(tenant_id, sequence_no);
CREATE INDEX audit_events_time_idx ON audit_events(tenant_id, occurred_at DESC);
CREATE INDEX audit_events_category_idx ON audit_events(tenant_id, category, occurred_at DESC);
CREATE INDEX audit_events_run_idx ON audit_events(run_id) WHERE run_id IS NOT NULL;

-- Tamper evidence: the ledger is append-only at the database level, not just
-- by convention. UPDATE and DELETE are rejected by trigger for every role.
CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_events is append-only (attempted %)', TG_OP
    USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER audit_events_no_update
  BEFORE UPDATE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();

CREATE TRIGGER audit_events_no_delete
  BEFORE DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();

-- ---------------------------------------------------------------------------
-- Assurance: controls, evidence, evaluations, certifications
-- ---------------------------------------------------------------------------
CREATE TABLE controls (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  control_id        text NOT NULL,
  domain            text NOT NULL,
  title             text NOT NULL,
  requirement       text NOT NULL,
  implementation    text NOT NULL DEFAULT '',
  weight            numeric(6,3) NOT NULL DEFAULT 1 CHECK (weight >= 0),
  critical          boolean NOT NULL DEFAULT false,
  applicable        boolean NOT NULL DEFAULT true,
  standard_mappings jsonb NOT NULL DEFAULT '[]'::jsonb,
  automated_test    text NOT NULL DEFAULT '',
  expected_result   text NOT NULL DEFAULT '',
  owner_team        text NOT NULL DEFAULT '',
  evidence_ttl_days integer NOT NULL DEFAULT 90 CHECK (evidence_ttl_days > 0),
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, control_id)
);

CREATE TABLE control_tests (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  control_id    text NOT NULL,
  test_id       text NOT NULL,
  test_type     text NOT NULL DEFAULT 'AUTOMATED'
                CHECK (test_type IN ('AUTOMATED', 'MANUAL', 'CONTINUOUS')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, control_id, test_id)
);

CREATE TABLE evidence (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  control_id     text NOT NULL,
  status         evidence_status NOT NULL,
  test_id        text NOT NULL DEFAULT '',
  expected_result text NOT NULL DEFAULT '',
  actual_result  text NOT NULL DEFAULT '',
  environment    text NOT NULL DEFAULT 'development',
  commit_sha     text NOT NULL DEFAULT '',
  artifact_uri   text NOT NULL DEFAULT '',
  artifact_hash  text NOT NULL DEFAULT '',
  owner_team     text NOT NULL DEFAULT '',
  collected_by   text NOT NULL DEFAULT 'evidence-engine',
  duration_ms    integer,
  expires_at     timestamptz,
  collected_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX evidence_control_idx ON evidence(tenant_id, control_id, collected_at DESC);

CREATE TABLE certifications (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  release_tag       text NOT NULL,
  commit_sha        text NOT NULL DEFAULT '',
  environment       text NOT NULL DEFAULT 'development',
  score             numeric(6,3) NOT NULL,
  certified         boolean NOT NULL DEFAULT false,
  critical_blockers jsonb NOT NULL DEFAULT '[]'::jsonb,
  domain_scores     jsonb NOT NULL DEFAULT '{}'::jsonb,
  report_uri        text NOT NULL DEFAULT '',
  bundle_hash       text NOT NULL DEFAULT '',
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE evaluations (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  suite_key        text NOT NULL,
  target_type      text NOT NULL CHECK (target_type IN ('AGENT', 'PROMPT', 'MODEL', 'SKILL', 'RAG')),
  target_key       text NOT NULL,
  target_version   text NOT NULL DEFAULT '',
  metrics          jsonb NOT NULL DEFAULT '{}'::jsonb,
  score            numeric(5,4) NOT NULL DEFAULT 0,
  threshold        numeric(5,4) NOT NULL DEFAULT 0.8,
  passed           boolean NOT NULL DEFAULT false,
  case_count       integer NOT NULL DEFAULT 0,
  failures         jsonb NOT NULL DEFAULT '[]'::jsonb,
  commit_sha       text NOT NULL DEFAULT '',
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX evaluations_target_idx ON evaluations(tenant_id, target_type, target_key, created_at DESC);

-- ---------------------------------------------------------------------------
-- Operations: incidents, alerts, cost, business outcomes
-- ---------------------------------------------------------------------------
CREATE TABLE incidents (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  incident_key   text NOT NULL,
  title          text NOT NULL,
  description    text NOT NULL DEFAULT '',
  severity       text NOT NULL DEFAULT 'SEV3' CHECK (severity IN ('SEV1', 'SEV2', 'SEV3', 'SEV4')),
  status         text NOT NULL DEFAULT 'OPEN'
                 CHECK (status IN ('OPEN', 'INVESTIGATING', 'MITIGATED', 'RESOLVED', 'CLOSED')),
  category       text NOT NULL DEFAULT 'OPERATIONAL',
  run_id         uuid REFERENCES runs(id) ON DELETE SET NULL,
  owner_user_id  uuid REFERENCES users(id),
  detected_at    timestamptz NOT NULL DEFAULT now(),
  resolved_at    timestamptz,
  root_cause     text NOT NULL DEFAULT '',
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, incident_key)
);

CREATE TABLE alerts (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  alert_type    text NOT NULL,
  severity      text NOT NULL DEFAULT 'WARNING'
                CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
  title         text NOT NULL,
  detail        jsonb NOT NULL DEFAULT '{}'::jsonb,
  source        text NOT NULL DEFAULT '',
  incident_id   uuid REFERENCES incidents(id) ON DELETE SET NULL,
  acknowledged_by uuid REFERENCES users(id),
  acknowledged_at timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX alerts_open_idx ON alerts(tenant_id, created_at DESC) WHERE acknowledged_at IS NULL;

CREATE TABLE cost_records (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id         uuid REFERENCES runs(id) ON DELETE SET NULL,
  run_step_id    uuid REFERENCES run_steps(id) ON DELETE SET NULL,
  category       text NOT NULL CHECK (category IN ('MODEL', 'TOOL', 'STORAGE', 'COMPUTE', 'RETRIEVAL')),
  provider       text NOT NULL DEFAULT '',
  model_key      text NOT NULL DEFAULT '',
  agent_key      text NOT NULL DEFAULT '',
  workflow_key   text NOT NULL DEFAULT '',
  user_id        uuid REFERENCES users(id),
  input_tokens   integer NOT NULL DEFAULT 0,
  output_tokens  integer NOT NULL DEFAULT 0,
  quantity       numeric(14,4) NOT NULL DEFAULT 0,
  cost_usd       numeric(12,6) NOT NULL DEFAULT 0,
  occurred_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cost_records_tenant_time_idx ON cost_records(tenant_id, occurred_at DESC);
CREATE INDEX cost_records_run_idx ON cost_records(run_id);

CREATE TABLE budgets (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  scope         text NOT NULL CHECK (scope IN
                ('TENANT', 'ORGANIZATION', 'USER', 'AGENT', 'WORKFLOW', 'MODEL', 'RUN')),
  scope_key     text NOT NULL DEFAULT '',
  period        text NOT NULL DEFAULT 'DAY' CHECK (period IN ('RUN', 'DAY', 'MONTH')),
  cost_cap_usd  numeric(12,4) NOT NULL DEFAULT 0,
  token_cap     bigint NOT NULL DEFAULT 0,
  alert_at_pct  numeric(5,2) NOT NULL DEFAULT 80,
  hard_stop     boolean NOT NULL DEFAULT true,
  fallback_model_key text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, scope, scope_key, period)
);

CREATE TABLE business_outcomes (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id          uuid REFERENCES runs(id) ON DELETE SET NULL,
  outcome_type    text NOT NULL CHECK (outcome_type IN
                  ('HOURS_SAVED', 'REVENUE_CREATED', 'REVENUE_PROTECTED',
                   'COST_AVOIDED', 'RISK_REDUCED', 'SLA_IMPROVED',
                   'RESPONSE_TIME_REDUCED', 'TASKS_AUTOMATED', 'FORECAST_ACCURACY',
                   'DECISION_LEADTIME_IMPROVED')),
  quantity        numeric(14,4) NOT NULL DEFAULT 0,
  unit            text NOT NULL DEFAULT 'unit',
  monetary_value_usd numeric(14,2) NOT NULL DEFAULT 0,
  -- Only auditable outcomes count toward ROI. Measured outcomes derive from
  -- recorded platform metrics; estimated ones are excluded from ROI totals.
  basis           text NOT NULL CHECK (basis IN ('MEASURED', 'ESTIMATED')),
  calculation     jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence_refs   jsonb NOT NULL DEFAULT '[]'::jsonb,
  verified_by     uuid REFERENCES users(id),
  verified_at     timestamptz,
  occurred_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX business_outcomes_tenant_idx ON business_outcomes(tenant_id, occurred_at DESC);
ALTER TABLE business_outcomes ADD CONSTRAINT measured_outcomes_need_evidence
  CHECK (basis <> 'MEASURED' OR jsonb_array_length(evidence_refs) > 0);

CREATE TABLE metric_samples (
  id            bigserial PRIMARY KEY,
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  metric        text NOT NULL,
  labels        jsonb NOT NULL DEFAULT '{}'::jsonb,
  value         double precision NOT NULL,
  recorded_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX metric_samples_lookup_idx ON metric_samples(tenant_id, metric, recorded_at DESC);

CREATE TABLE traces (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  trace_id       text NOT NULL,
  span_id        text NOT NULL,
  parent_span_id text NOT NULL DEFAULT '',
  name           text NOT NULL,
  kind           text NOT NULL DEFAULT 'INTERNAL',
  run_id         uuid REFERENCES runs(id) ON DELETE CASCADE,
  attributes     jsonb NOT NULL DEFAULT '{}'::jsonb,
  status         text NOT NULL DEFAULT 'OK',
  started_at     timestamptz NOT NULL,
  ended_at       timestamptz,
  duration_ms    integer,
  UNIQUE (trace_id, span_id)
);
CREATE INDEX traces_run_idx ON traces(run_id, started_at);
CREATE INDEX traces_trace_idx ON traces(tenant_id, trace_id);

CREATE TABLE security_findings (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  finding_type   text NOT NULL,
  severity       text NOT NULL DEFAULT 'MEDIUM'
                 CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
  source         text NOT NULL DEFAULT '',
  run_id         uuid REFERENCES runs(id) ON DELETE SET NULL,
  detail         jsonb NOT NULL DEFAULT '{}'::jsonb,
  blocked        boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX security_findings_tenant_idx ON security_findings(tenant_id, created_at DESC);
