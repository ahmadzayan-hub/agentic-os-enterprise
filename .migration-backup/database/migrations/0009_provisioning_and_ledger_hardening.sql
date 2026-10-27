-- 0009 Tenant provisioning path and audit ledger hardening.
--
-- Corrects two defects found by empirical verification of 0008:
--
-- 1. ``FORCE ROW LEVEL SECURITY`` applies to the table owner too, so no role
--    could create the first organization/tenant — provisioning was impossible.
--    Rather than weakening any policy, provisioning is confined to a single
--    dedicated NOLOGIN role that carries BYPASSRLS, reachable only through the
--    two SECURITY DEFINER functions below. The application role never does,
--    which ``tests/tenant_isolation`` asserts on every run.
--
-- 2. The append-only triggers on ``audit_events`` are FOR EACH ROW, so a
--    TRUNCATE would have emptied the ledger without firing them.

-- --- 1. provisioning grants -------------------------------------------------
-- The roles themselves are created by database/bootstrap/00_cluster_bootstrap.sql
-- (a superuser operation). This migration only grants privileges on objects.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agentic_provisioner') THEN
    RAISE EXCEPTION
      'role agentic_provisioner is missing - run database/bootstrap/00_cluster_bootstrap.sql as a superuser first';
  END IF;
END $$;

-- CREATE is required only so the SECURITY DEFINER functions below can be
-- owned by this role; it holds no login and is unreachable interactively.
GRANT USAGE, CREATE ON SCHEMA public TO agentic_provisioner;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO agentic_provisioner;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agentic_provisioner;
REVOKE UPDATE, DELETE ON audit_events FROM agentic_provisioner;

-- --- 2. ledger truncate protection -----------------------------------------
CREATE OR REPLACE FUNCTION audit_events_no_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_events is append-only (attempted TRUNCATE)'
    USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER audit_events_no_truncate
  BEFORE TRUNCATE ON audit_events
  FOR EACH STATEMENT EXECUTE FUNCTION audit_events_no_truncate();

REVOKE TRUNCATE ON audit_events FROM agentic_app;

-- --- 3. ledger verification helper -----------------------------------------
-- Recomputes the hash chain for a tenant and reports the first broken link.
-- Used by the Evidence Engine control AUD-002.
CREATE OR REPLACE FUNCTION audit_verify_chain(p_tenant uuid)
RETURNS TABLE (checked bigint, broken_at bigint, expected_prev text, found_prev text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r record;
  v_prev text := repeat('0', 64);
  v_count bigint := 0;
BEGIN
  broken_at := NULL;
  FOR r IN
    SELECT a.sequence_no, a.previous_hash, a.entry_hash
    FROM audit_events a WHERE a.tenant_id = p_tenant ORDER BY a.sequence_no
  LOOP
    v_count := v_count + 1;
    IF r.previous_hash <> v_prev THEN
      checked := v_count; broken_at := r.sequence_no;
      expected_prev := v_prev; found_prev := r.previous_hash;
      RETURN NEXT; RETURN;
    END IF;
    v_prev := r.entry_hash;
  END LOOP;
  checked := v_count; expected_prev := v_prev; found_prev := v_prev;
  RETURN NEXT;
END;
$$;

GRANT EXECUTE ON FUNCTION audit_verify_chain(uuid) TO agentic_app;

-- --- 4. provisioning surface ------------------------------------------------
-- Creating a tenant is the one operation that legitimately precedes a tenant
-- context. It is exposed as a single audited function rather than as broad
-- write access, so the privileged surface is one call wide.
CREATE OR REPLACE FUNCTION platform_provision_tenant(
  p_org_slug text, p_org_name text, p_tenant_slug text, p_tenant_name text,
  p_region text DEFAULT 'global'
) RETURNS TABLE (organization_id uuid, tenant_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_org uuid;
  v_tenant uuid;
BEGIN
  SELECT id INTO v_org FROM organizations WHERE slug = p_org_slug;
  IF v_org IS NULL THEN
    INSERT INTO organizations (slug, name, region)
    VALUES (p_org_slug, p_org_name, p_region) RETURNING id INTO v_org;
  END IF;

  SELECT id INTO v_tenant FROM tenants
   WHERE organization_id = v_org AND slug = p_tenant_slug;
  IF v_tenant IS NULL THEN
    INSERT INTO tenants (organization_id, slug, name, region)
    VALUES (v_org, p_tenant_slug, p_tenant_name, p_region) RETURNING id INTO v_tenant;
  END IF;

  organization_id := v_org; tenant_id := v_tenant;
  RETURN NEXT;
END;
$$;

ALTER FUNCTION platform_provision_tenant(text, text, text, text, text) OWNER TO agentic_provisioner;
REVOKE EXECUTE ON FUNCTION platform_provision_tenant(text, text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform_provision_tenant(text, text, text, text, text) TO agentic_app;

-- The bootstrap authenticator must also run as the bypass role, since users
-- are RLS-protected and login precedes tenant binding.
ALTER FUNCTION auth_bootstrap_user(text) OWNER TO agentic_provisioner;
ALTER FUNCTION auth_record_login_attempt(uuid, boolean) OWNER TO agentic_provisioner;
ALTER FUNCTION audit_next_sequence(uuid) OWNER TO agentic_provisioner;
