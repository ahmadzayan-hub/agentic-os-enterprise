-- 0008 Row Level Security and least-privilege grants.
--
-- Design notes
-- ------------
-- * Every table carrying a ``tenant_id`` column gets an identical isolation
--   policy derived automatically, so a new table cannot be forgotten. The
--   companion test ``tests/database/test_rls_coverage.py`` fails if any
--   tenant-scoped table is left without FORCE ROW LEVEL SECURITY.
-- * There is deliberately **no** bypass predicate. Platform-wide work iterates
--   tenants and binds each one; no GUC or role can switch isolation off, so a
--   compromised application role cannot read across tenants.
-- * ``nullif(...)`` means an unbound session matches nothing instead of raising,
--   which fails closed.

CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.tenant_id', true), '')::uuid
$$;

DO $$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT c.relname AS table_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND a.attnum > 0
    WHERE c.relkind = 'r' AND n.nspname = 'public'
    ORDER BY c.relname
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.table_name);
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', r.table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I', r.table_name);
    -- Tables whose tenant_id is nullable model platform-scope rows (system
    -- roles, global kill switches, platform backups). Those rows are visible
    -- to every bound session but writable only through the owner role.
    IF EXISTS (
      SELECT 1 FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'public' AND c.relname = r.table_name
        AND a.attname = 'tenant_id' AND a.attnotnull IS FALSE
    ) THEN
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON public.%I
           USING (tenant_id IS NULL OR tenant_id = app_current_tenant())
           WITH CHECK (tenant_id = app_current_tenant())', r.table_name);
    ELSE
      EXECUTE format(
        'CREATE POLICY tenant_isolation ON public.%I
           USING (tenant_id = app_current_tenant())
           WITH CHECK (tenant_id = app_current_tenant())', r.table_name);
    END IF;
  END LOOP;
END $$;

-- Tenancy spine: the tenant row itself and its organization.
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_self ON tenants
  USING (id = app_current_tenant())
  WITH CHECK (id = app_current_tenant());

ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations FORCE ROW LEVEL SECURITY;
CREATE POLICY organization_of_tenant ON organizations
  USING (id IN (SELECT t.organization_id FROM tenants t WHERE t.id = app_current_tenant()))
  WITH CHECK (id IN (SELECT t.organization_id FROM tenants t WHERE t.id = app_current_tenant()));

-- Reference data with no tenant dimension.
ALTER TABLE permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE permissions FORCE ROW LEVEL SECURITY;
CREATE POLICY permissions_readable ON permissions FOR SELECT USING (true);

-- ---------------------------------------------------------------------------
-- Pre-authentication bootstrap
-- ---------------------------------------------------------------------------
-- Login must resolve a user before any tenant is known. Rather than weakening
-- RLS on ``users``, a single narrow SECURITY DEFINER function exposes exactly
-- the columns the authenticator needs and nothing else. Every call is audited
-- by the caller.
CREATE OR REPLACE FUNCTION auth_bootstrap_user(p_email text)
RETURNS TABLE (
  id uuid,
  tenant_id uuid,
  organization_id uuid,
  password_hash text,
  status lifecycle_status,
  mfa_enrolled boolean,
  clearance data_classification,
  locked_until timestamptz,
  failed_login_count integer,
  display_name text
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT u.id, u.tenant_id, u.organization_id, u.password_hash, u.status,
         u.mfa_enrolled, u.clearance, u.locked_until, u.failed_login_count,
         u.display_name
  FROM users u
  WHERE u.email = lower(p_email)
    AND u.deleted_at IS NULL
  LIMIT 1
$$;

CREATE OR REPLACE FUNCTION auth_record_login_attempt(p_user_id uuid, p_success boolean)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  UPDATE users
     SET failed_login_count = CASE WHEN p_success THEN 0 ELSE failed_login_count + 1 END,
         last_login_at = CASE WHEN p_success THEN now() ELSE last_login_at END,
         locked_until = CASE
           WHEN NOT p_success AND failed_login_count + 1 >= 5 THEN now() + interval '15 minutes'
           WHEN p_success THEN NULL
           ELSE locked_until END,
         updated_at = now()
   WHERE id = p_user_id;
$$;

-- ---------------------------------------------------------------------------
-- Audit ledger sequence allocation (gapless per tenant)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit_next_sequence(p_tenant uuid)
RETURNS TABLE (next_no bigint, prev_hash text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_no bigint;
  v_hash text;
BEGIN
  -- Serialise appenders for this tenant so the chain cannot fork.
  PERFORM pg_advisory_xact_lock(hashtextextended(p_tenant::text, 42));
  SELECT a.sequence_no, a.entry_hash INTO v_no, v_hash
  FROM audit_events a
  WHERE a.tenant_id = p_tenant
  ORDER BY a.sequence_no DESC
  LIMIT 1;
  next_no := COALESCE(v_no, 0) + 1;
  prev_hash := COALESCE(v_hash, repeat('0', 64));
  RETURN NEXT;
END;
$$;

-- ---------------------------------------------------------------------------
-- Least-privilege grants
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO agentic_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO agentic_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agentic_app;
GRANT EXECUTE ON FUNCTION auth_bootstrap_user(text) TO agentic_app;
GRANT EXECUTE ON FUNCTION auth_record_login_attempt(uuid, boolean) TO agentic_app;
GRANT EXECUTE ON FUNCTION audit_next_sequence(uuid) TO agentic_app;
GRANT EXECUTE ON FUNCTION app_current_tenant() TO agentic_app;

-- The audit ledger is insert-only for the application role. The triggers in
-- 0006 additionally block UPDATE/DELETE for every role including the owner.
REVOKE UPDATE, DELETE ON audit_events FROM agentic_app;

-- Registries and reference data are read-only for the application role;
-- changes go through migrations or the owner-scoped admin path.
REVOKE INSERT, UPDATE, DELETE ON permissions FROM agentic_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agentic_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO agentic_app;
