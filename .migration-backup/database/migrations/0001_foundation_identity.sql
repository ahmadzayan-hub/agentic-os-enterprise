-- 0001 Foundation and identity plane.
-- Creates extensions, shared enums, the tenancy spine and the RBAC/ABAC model.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Shared enumerations
-- ---------------------------------------------------------------------------
CREATE TYPE data_classification AS ENUM ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED');
CREATE TYPE autonomy_level AS ENUM ('A0', 'A1', 'A2', 'A3', 'A4');
CREATE TYPE risk_class AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL');
CREATE TYPE lifecycle_status AS ENUM ('DRAFT', 'ACTIVE', 'DEPRECATED', 'RETIRED', 'QUARANTINED');
CREATE TYPE run_status AS ENUM (
  'PENDING', 'PLANNING', 'AWAITING_APPROVAL', 'RUNNING',
  'SUCCEEDED', 'FAILED', 'CANCELLED', 'COMPENSATED', 'TIMED_OUT'
);
CREATE TYPE approval_status AS ENUM (
  'PENDING', 'APPROVED', 'REJECTED', 'CHANGES_REQUESTED', 'EXPIRED', 'CANCELLED'
);
CREATE TYPE policy_effect AS ENUM ('ALLOW', 'DENY', 'REQUIRE_APPROVAL', 'MONITOR');
CREATE TYPE evidence_status AS ENUM (
  'PLANNED', 'DESIGNED', 'IMPLEMENTED', 'TESTED', 'VERIFIED',
  'PRODUCTION_PROVEN', 'EXPIRED', 'FAILED', 'NOT_EVIDENCED'
);

-- ---------------------------------------------------------------------------
-- Tenancy spine
-- ---------------------------------------------------------------------------
CREATE TABLE organizations (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug            text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name            text NOT NULL,
  region          text NOT NULL DEFAULT 'global',
  status          lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  settings        jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz
);

CREATE TABLE tenants (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  slug                text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name                text NOT NULL,
  region              text NOT NULL DEFAULT 'global',
  data_residency      text NOT NULL DEFAULT 'global',
  status              lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  default_classification data_classification NOT NULL DEFAULT 'INTERNAL',
  retention_days      integer NOT NULL DEFAULT 730 CHECK (retention_days > 0),
  daily_cost_cap_usd  numeric(12,4) NOT NULL DEFAULT 250.0 CHECK (daily_cost_cap_usd >= 0),
  settings            jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  deleted_at          timestamptz,
  UNIQUE (organization_id, slug)
);
CREATE INDEX tenants_org_idx ON tenants(organization_id) WHERE deleted_at IS NULL;

-- ---------------------------------------------------------------------------
-- Principals
-- ---------------------------------------------------------------------------
CREATE TABLE users (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
  organization_id     uuid NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  email               text NOT NULL CHECK (email = lower(email) AND position('@' in email) > 1),
  display_name        text NOT NULL DEFAULT '',
  password_hash       text,
  external_subject    text,
  identity_provider   text NOT NULL DEFAULT 'local',
  mfa_enrolled        boolean NOT NULL DEFAULT false,
  mfa_secret_ref      text,
  clearance           data_classification NOT NULL DEFAULT 'INTERNAL',
  status              lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  attributes          jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_login_at       timestamptz,
  failed_login_count  integer NOT NULL DEFAULT 0,
  locked_until        timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  deleted_at          timestamptz,
  UNIQUE (tenant_id, email)
);
CREATE UNIQUE INDEX users_external_subject_idx
  ON users(identity_provider, external_subject)
  WHERE external_subject IS NOT NULL;
CREATE INDEX users_tenant_idx ON users(tenant_id) WHERE deleted_at IS NULL;

CREATE TABLE groups (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  slug            text NOT NULL,
  name            text NOT NULL,
  description     text NOT NULL DEFAULT '',
  attributes      jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, slug)
);

CREATE TABLE user_groups (
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  group_id   uuid NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, group_id)
);

CREATE TABLE permissions (
  id          text PRIMARY KEY CHECK (id ~ '^[a-z_]+:[a-z_*]+$'),
  description text NOT NULL,
  resource    text NOT NULL,
  action      text NOT NULL,
  risk        risk_class NOT NULL DEFAULT 'LOW'
);

CREATE TABLE roles (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid REFERENCES tenants(id) ON DELETE CASCADE,
  slug            text NOT NULL,
  name            text NOT NULL,
  description     text NOT NULL DEFAULT '',
  is_system       boolean NOT NULL DEFAULT false,
  requires_mfa    boolean NOT NULL DEFAULT false,
  max_autonomy    autonomy_level NOT NULL DEFAULT 'A2',
  created_at      timestamptz NOT NULL DEFAULT now()
);
-- System roles are tenant-independent (tenant_id IS NULL); tenant roles are scoped.
CREATE UNIQUE INDEX roles_system_slug_idx ON roles(slug) WHERE tenant_id IS NULL;
CREATE UNIQUE INDEX roles_tenant_slug_idx ON roles(tenant_id, slug) WHERE tenant_id IS NOT NULL;

CREATE TABLE role_permissions (
  role_id       uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  permission_id text NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
  PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE user_roles (
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id    uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  granted_by uuid REFERENCES users(id),
  granted_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz,
  PRIMARY KEY (user_id, role_id)
);
CREATE INDEX user_roles_tenant_idx ON user_roles(tenant_id);

CREATE TABLE sessions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  refresh_token_hash text NOT NULL,
  mfa_satisfied     boolean NOT NULL DEFAULT false,
  ip_address        inet,
  user_agent        text NOT NULL DEFAULT '',
  issued_at         timestamptz NOT NULL DEFAULT now(),
  expires_at        timestamptz NOT NULL,
  revoked_at        timestamptz
);
CREATE INDEX sessions_user_idx ON sessions(user_id, expires_at DESC);
CREATE UNIQUE INDEX sessions_refresh_idx ON sessions(refresh_token_hash);

-- Service and workload identities (non-human principals).
CREATE TABLE service_identities (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  slug           text NOT NULL,
  kind           text NOT NULL CHECK (kind IN ('SERVICE', 'WORKLOAD', 'AGENT', 'CONNECTOR')),
  description    text NOT NULL DEFAULT '',
  secret_hash    text,
  scopes         text[] NOT NULL DEFAULT '{}',
  status         lifecycle_status NOT NULL DEFAULT 'ACTIVE',
  created_at     timestamptz NOT NULL DEFAULT now(),
  rotated_at     timestamptz,
  UNIQUE (tenant_id, slug)
);
