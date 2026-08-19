-- 0012 Multi-factor enrolment.
--
-- TOTP secrets are stored as KMS envelopes bound to the user id as additional
-- authenticated data, so a ciphertext lifted from this table cannot be replayed
-- against a different user. ``last_counter`` gives replay protection: a code
-- accepted for one time step is never accepted twice.

CREATE TABLE user_mfa (
  user_id        uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  method         text NOT NULL DEFAULT 'TOTP' CHECK (method IN ('TOTP', 'WEBAUTHN', 'EXTERNAL_IDP')),
  secret_ciphertext text NOT NULL DEFAULT '',
  kms_backend    text NOT NULL DEFAULT 'local',
  digits         integer NOT NULL DEFAULT 6 CHECK (digits BETWEEN 6 AND 8),
  period_seconds integer NOT NULL DEFAULT 30 CHECK (period_seconds > 0),
  last_counter   bigint NOT NULL DEFAULT 0,
  verified_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE user_mfa ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_mfa FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON user_mfa
  USING (tenant_id = app_current_tenant())
  WITH CHECK (tenant_id = app_current_tenant());

GRANT SELECT, INSERT, UPDATE, DELETE ON user_mfa TO agentic_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON user_mfa TO agentic_provisioner;

-- Login precedes tenant binding, so enrolment lookup needs the same narrow
-- bypass treatment as auth_bootstrap_user.
CREATE OR REPLACE FUNCTION auth_bootstrap_mfa(p_user_id uuid)
RETURNS TABLE (
  method text, secret_ciphertext text, kms_backend text,
  digits integer, period_seconds integer, last_counter bigint
)
LANGUAGE sql SECURITY DEFINER SET search_path = public STABLE AS $$
  SELECT m.method, m.secret_ciphertext, m.kms_backend, m.digits,
         m.period_seconds, m.last_counter
  FROM user_mfa m WHERE m.user_id = p_user_id
$$;

CREATE OR REPLACE FUNCTION auth_record_mfa_counter(p_user_id uuid, p_counter bigint)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_updated integer;
BEGIN
  -- Strictly increasing: a replayed code updates no row and returns false.
  UPDATE user_mfa SET last_counter = p_counter, verified_at = now()
   WHERE user_id = p_user_id AND p_counter > last_counter;
  GET DIAGNOSTICS v_updated = ROW_COUNT;
  RETURN v_updated > 0;
END;
$$;

ALTER FUNCTION auth_bootstrap_mfa(uuid) OWNER TO agentic_provisioner;
ALTER FUNCTION auth_record_mfa_counter(uuid, bigint) OWNER TO agentic_provisioner;
REVOKE EXECUTE ON FUNCTION auth_bootstrap_mfa(uuid) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION auth_record_mfa_counter(uuid, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_bootstrap_mfa(uuid) TO agentic_app;
GRANT EXECUTE ON FUNCTION auth_record_mfa_counter(uuid, bigint) TO agentic_app;

-- Role-driven MFA requirement: a user holding any role with requires_mfa must
-- present a second factor. This is the single source of truth; configuration
-- can only add to it, never remove it.
CREATE OR REPLACE FUNCTION auth_user_requires_mfa(p_user_id uuid)
RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = public STABLE AS $$
  SELECT COALESCE(bool_or(r.requires_mfa), false)
  FROM user_roles ur JOIN roles r ON r.id = ur.role_id
  WHERE ur.user_id = p_user_id
    AND (ur.expires_at IS NULL OR ur.expires_at > now())
$$;
ALTER FUNCTION auth_user_requires_mfa(uuid) OWNER TO agentic_provisioner;
GRANT EXECUTE ON FUNCTION auth_user_requires_mfa(uuid) TO agentic_app;
