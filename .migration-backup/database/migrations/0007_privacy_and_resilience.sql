-- 0007 Privacy, data lifecycle and resilience records.

CREATE TABLE retention_policies (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  resource_type  text NOT NULL,
  classification data_classification,
  retention_days integer NOT NULL CHECK (retention_days > 0),
  action_on_expiry text NOT NULL DEFAULT 'DELETE'
                 CHECK (action_on_expiry IN ('DELETE', 'ANONYMIZE', 'ARCHIVE')),
  legal_basis    text NOT NULL DEFAULT '',
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, resource_type, classification)
);

CREATE TABLE legal_holds (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  hold_key      text NOT NULL,
  reason        text NOT NULL,
  resource_type text NOT NULL,
  resource_filter jsonb NOT NULL DEFAULT '{}'::jsonb,
  requested_by  uuid REFERENCES users(id),
  active        boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  released_at   timestamptz,
  UNIQUE (tenant_id, hold_key)
);

CREATE TABLE data_subject_requests (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  request_type  text NOT NULL CHECK (request_type IN ('ACCESS', 'EXPORT', 'DELETE', 'RECTIFY')),
  subject_email text NOT NULL,
  subject_user_id uuid REFERENCES users(id),
  status        text NOT NULL DEFAULT 'RECEIVED'
                CHECK (status IN ('RECEIVED', 'VERIFYING', 'IN_PROGRESS', 'COMPLETED', 'REJECTED', 'BLOCKED_BY_HOLD')),
  requested_by  uuid REFERENCES users(id),
  due_at        timestamptz,
  result_uri    text NOT NULL DEFAULT '',
  affected_records jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz
);

CREATE TABLE pii_inventory (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  resource_type text NOT NULL,
  resource_id   text NOT NULL,
  pii_type      text NOT NULL,
  detector      text NOT NULL DEFAULT '',
  confidence    numeric(5,4) NOT NULL DEFAULT 0,
  occurrences   integer NOT NULL DEFAULT 1,
  redacted      boolean NOT NULL DEFAULT false,
  detected_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pii_inventory_lookup_idx ON pii_inventory(tenant_id, resource_type, resource_id);

CREATE TABLE processing_records (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  activity       text NOT NULL,
  purpose        text NOT NULL,
  legal_basis    text NOT NULL,
  data_categories text[] NOT NULL DEFAULT '{}',
  subject_categories text[] NOT NULL DEFAULT '{}',
  recipients     text[] NOT NULL DEFAULT '{}',
  cross_border   boolean NOT NULL DEFAULT false,
  retention      text NOT NULL DEFAULT '',
  controller     text NOT NULL DEFAULT '',
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, activity)
);

-- ---------------------------------------------------------------------------
-- Resilience: backup and restore evidence
-- ---------------------------------------------------------------------------
CREATE TABLE backup_records (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid REFERENCES tenants(id) ON DELETE CASCADE,
  backup_type   text NOT NULL CHECK (backup_type IN ('DATABASE', 'OBJECT_STORE', 'CONFIG', 'KEYS')),
  scope         text NOT NULL DEFAULT 'full',
  artifact_uri  text NOT NULL DEFAULT '',
  artifact_hash text NOT NULL DEFAULT '',
  size_bytes    bigint NOT NULL DEFAULT 0,
  status        text NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'COMPLETED', 'FAILED')),
  started_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz
);

CREATE TABLE restore_tests (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid REFERENCES tenants(id) ON DELETE CASCADE,
  backup_id      uuid REFERENCES backup_records(id) ON DELETE SET NULL,
  environment    text NOT NULL DEFAULT 'test',
  outcome        text NOT NULL CHECK (outcome IN ('SUCCESS', 'PARTIAL', 'FAILURE', 'NOT_RUN')),
  rpo_achieved_seconds integer,
  rto_achieved_seconds integer,
  verified_rows  bigint NOT NULL DEFAULT 0,
  notes          text NOT NULL DEFAULT '',
  executed_by    text NOT NULL DEFAULT '',
  executed_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Object store index (metadata for uploaded blobs)
-- ---------------------------------------------------------------------------
CREATE TABLE object_store_entries (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  storage_key   text NOT NULL,
  content_hash  text NOT NULL,
  byte_size     bigint NOT NULL DEFAULT 0,
  mime_type     text NOT NULL DEFAULT 'application/octet-stream',
  quarantined   boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, storage_key)
);
