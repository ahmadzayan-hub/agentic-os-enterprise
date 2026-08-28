-- 0010 Fix ambiguous column reference in platform_provision_tenant.
--
-- The OUT parameter ``organization_id`` shadowed ``tenants.organization_id``
-- inside the function body, so provisioning failed at runtime. OUT parameters
-- are renamed with an ``out_`` prefix and every column reference is qualified.

DROP FUNCTION IF EXISTS platform_provision_tenant(text, text, text, text, text);

CREATE FUNCTION platform_provision_tenant(
  p_org_slug text, p_org_name text, p_tenant_slug text, p_tenant_name text,
  p_region text DEFAULT 'global'
) RETURNS TABLE (out_organization_id uuid, out_tenant_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_org uuid;
  v_tenant uuid;
BEGIN
  SELECT o.id INTO v_org FROM organizations o WHERE o.slug = p_org_slug;
  IF v_org IS NULL THEN
    INSERT INTO organizations (slug, name, region)
    VALUES (p_org_slug, p_org_name, p_region)
    RETURNING organizations.id INTO v_org;
  END IF;

  SELECT t.id INTO v_tenant FROM tenants t
   WHERE t.organization_id = v_org AND t.slug = p_tenant_slug;
  IF v_tenant IS NULL THEN
    INSERT INTO tenants (organization_id, slug, name, region)
    VALUES (v_org, p_tenant_slug, p_tenant_name, p_region)
    RETURNING tenants.id INTO v_tenant;
  END IF;

  out_organization_id := v_org;
  out_tenant_id := v_tenant;
  RETURN NEXT;
END;
$$;

ALTER FUNCTION platform_provision_tenant(text, text, text, text, text) OWNER TO agentic_provisioner;
REVOKE EXECUTE ON FUNCTION platform_provision_tenant(text, text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION platform_provision_tenant(text, text, text, text, text) TO agentic_app;
