-- 0004 Tool plane: tool registry, connectors, credentials, MCP registry, kill switches.

CREATE TABLE tools (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  tool_key           text NOT NULL CHECK (tool_key ~ '^[a-z][a-z0-9_.-]{2,79}$'),
  name               text NOT NULL,
  description        text NOT NULL DEFAULT '',
  owner_team         text NOT NULL DEFAULT '',
  kind               text NOT NULL CHECK (kind IN ('BUILTIN', 'HTTP', 'MCP', 'DATABASE', 'INTERNAL')),
  connector_key      text NOT NULL DEFAULT '',
  parameter_schema   jsonb NOT NULL DEFAULT '{}'::jsonb,
  result_schema      jsonb NOT NULL DEFAULT '{}'::jsonb,
  scopes             text[] NOT NULL DEFAULT '{}',
  risk_class         risk_class NOT NULL DEFAULT 'MEDIUM',
  min_autonomy       autonomy_level NOT NULL DEFAULT 'A3',
  side_effect        text NOT NULL DEFAULT 'READ'
                     CHECK (side_effect IN ('READ', 'WRITE', 'DELETE', 'EXTERNAL', 'FINANCIAL')),
  reversibility      text NOT NULL DEFAULT 'REVERSIBLE'
                     CHECK (reversibility IN ('REVERSIBLE', 'PARTIAL', 'IRREVERSIBLE')),
  max_classification data_classification NOT NULL DEFAULT 'INTERNAL',
  rate_limit_per_minute integer NOT NULL DEFAULT 60,
  timeout_seconds    integer NOT NULL DEFAULT 30,
  requires_approval  boolean NOT NULL DEFAULT false,
  verification_mode  text NOT NULL DEFAULT 'NONE'
                     CHECK (verification_mode IN ('NONE', 'ECHO', 'READ_BACK', 'RECEIPT')),
  status             lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  implementation_status text NOT NULL DEFAULT 'IMPLEMENTED'
                     CHECK (implementation_status IN ('IMPLEMENTED', 'NOT_IMPLEMENTED')),
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, tool_key)
);

CREATE TABLE tool_calls (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  run_id           uuid REFERENCES runs(id) ON DELETE SET NULL,
  run_step_id      uuid REFERENCES run_steps(id) ON DELETE SET NULL,
  tool_key         text NOT NULL,
  agent_key        text NOT NULL DEFAULT '',
  user_id          uuid REFERENCES users(id),
  correlation_id   text NOT NULL,
  idempotency_key  text NOT NULL,
  gateway_decision text NOT NULL
                   CHECK (gateway_decision IN ('ALLOWED', 'DENIED', 'APPROVAL_REQUIRED', 'ERROR')),
  denial_reason    text NOT NULL DEFAULT '',
  denial_stage     text NOT NULL DEFAULT '',
  parameters_hash  text NOT NULL,
  parameters_redacted jsonb NOT NULL DEFAULT '{}'::jsonb,
  result_hash      text,
  result_redacted  jsonb,
  verification_status text NOT NULL DEFAULT 'NOT_APPLICABLE'
                   CHECK (verification_status IN
                          ('NOT_APPLICABLE', 'PENDING', 'VERIFIED', 'FAILED')),
  status_code      integer,
  error_class      text,
  latency_ms       integer,
  cost_usd         numeric(12,6) NOT NULL DEFAULT 0,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX tool_calls_run_idx ON tool_calls(run_id);
CREATE INDEX tool_calls_tenant_time_idx ON tool_calls(tenant_id, created_at DESC);
CREATE UNIQUE INDEX tool_calls_idem_idx ON tool_calls(tenant_id, tool_key, idempotency_key);

CREATE TABLE connectors (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  connector_key   text NOT NULL,
  name            text NOT NULL,
  provider        text NOT NULL,
  base_url        text NOT NULL DEFAULT '',
  auth_method     text NOT NULL DEFAULT 'NONE'
                  CHECK (auth_method IN ('NONE', 'API_KEY', 'OAUTH2', 'MTLS', 'BASIC', 'AWS_SIGV4')),
  network_destinations text[] NOT NULL DEFAULT '{}',
  data_classification data_classification NOT NULL DEFAULT 'INTERNAL',
  owner_team      text NOT NULL DEFAULT '',
  status          lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  last_security_review timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, connector_key)
);

-- Credentials are stored as KMS envelopes; the plaintext never leaves the
-- secret broker and is never placed in model context.
CREATE TABLE connector_credentials (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  connector_id    uuid NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
  credential_key  text NOT NULL,
  ciphertext      text NOT NULL,
  kms_backend     text NOT NULL DEFAULT 'local',
  fingerprint     text NOT NULL,
  scopes          text[] NOT NULL DEFAULT '{}',
  expires_at      timestamptz,
  rotated_at      timestamptz,
  rotation_due_at timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (connector_id, credential_key)
);

-- ---------------------------------------------------------------------------
-- MCP registry
-- ---------------------------------------------------------------------------
CREATE TABLE mcp_servers (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  server_key          text NOT NULL,
  name                text NOT NULL,
  provider            text NOT NULL DEFAULT '',
  owner_team          text NOT NULL DEFAULT '',
  version             text NOT NULL DEFAULT '0.0.0',
  endpoint            text NOT NULL,
  transport           text NOT NULL DEFAULT 'http' CHECK (transport IN ('http', 'stdio', 'sse')),
  trust_class         text NOT NULL DEFAULT 'EXPERIMENTAL'
                      CHECK (trust_class IN
                             ('TRUSTED_INTERNAL', 'APPROVED_EXTERNAL', 'EXPERIMENTAL',
                              'DISABLED', 'QUARANTINED')),
  authorization_method text NOT NULL DEFAULT 'NONE'
                      CHECK (authorization_method IN ('NONE', 'API_KEY', 'OAUTH2', 'MTLS')),
  data_classification data_classification NOT NULL DEFAULT 'INTERNAL',
  network_destinations text[] NOT NULL DEFAULT '{}',
  allowed_agents      text[] NOT NULL DEFAULT '{}',
  allowed_roles       text[] NOT NULL DEFAULT '{}',
  scopes              text[] NOT NULL DEFAULT '{}',
  capabilities        jsonb NOT NULL DEFAULT '{}'::jsonb,
  forward_user_token  boolean NOT NULL DEFAULT false,
  last_security_review timestamptz,
  last_used_at        timestamptz,
  status              lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, server_key)
);
-- Constitution rule 7: no agent receives unrestricted credentials. Token
-- forwarding is only ever permitted for internally operated servers.
ALTER TABLE mcp_servers ADD CONSTRAINT mcp_no_untrusted_token_forwarding
  CHECK (forward_user_token = false OR trust_class = 'TRUSTED_INTERNAL');

CREATE TABLE mcp_tools (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  mcp_server_id  uuid NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
  tool_name      text NOT NULL,
  description    text NOT NULL DEFAULT '',
  input_schema   jsonb NOT NULL DEFAULT '{}'::jsonb,
  risk_class     risk_class NOT NULL DEFAULT 'MEDIUM',
  approved       boolean NOT NULL DEFAULT false,
  approved_by    uuid REFERENCES users(id),
  approved_at    timestamptz,
  discovered_at  timestamptz NOT NULL DEFAULT now(),
  schema_hash    text NOT NULL DEFAULT '',
  UNIQUE (mcp_server_id, tool_name)
);

-- ---------------------------------------------------------------------------
-- Kill switches
-- ---------------------------------------------------------------------------
CREATE TABLE kill_switches (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid REFERENCES tenants(id) ON DELETE CASCADE,
  scope         text NOT NULL CHECK (scope IN
                 ('GLOBAL', 'TENANT', 'AGENT', 'MODEL', 'TOOL', 'CONNECTOR',
                  'WORKFLOW', 'READ_ONLY')),
  target_key    text NOT NULL DEFAULT '',
  engaged       boolean NOT NULL DEFAULT false,
  reason        text NOT NULL DEFAULT '',
  engaged_by    uuid REFERENCES users(id),
  engaged_at    timestamptz,
  released_by   uuid REFERENCES users(id),
  released_at   timestamptz,
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX kill_switches_global_idx
  ON kill_switches(scope, target_key) WHERE tenant_id IS NULL;
CREATE UNIQUE INDEX kill_switches_tenant_idx
  ON kill_switches(tenant_id, scope, target_key) WHERE tenant_id IS NOT NULL;
