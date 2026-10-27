-- Cluster bootstrap — run once by a superuser before any migration.
--
-- Roles and database creation are cluster-level operations and deliberately
-- live outside the migration history: migrations run as ``agentic_owner``,
-- which has no CREATEROLE privilege by design.
--
--   psql -U postgres -f database/bootstrap/00_cluster_bootstrap.sql
--   psql -U postgres -d agentic -f database/bootstrap/01_extensions.sql
--
-- Passwords here are development placeholders. In staging/production the
-- roles are created by Terraform with secrets drawn from the secret manager.

DO $$
BEGIN
  -- Migration/DDL owner. Member of agentic_provisioner so that migrations and
  -- tenant provisioning can write the tenancy spine.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agentic_owner') THEN
    CREATE ROLE agentic_owner LOGIN PASSWORD 'agentic_owner';
  END IF;

  -- Least-privilege application role. Never BYPASSRLS, never superuser.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agentic_app') THEN
    CREATE ROLE agentic_app LOGIN PASSWORD 'agentic_app';
  END IF;

  -- The single role permitted to bypass row level security. It exists only so
  -- that tenant provisioning and pre-authentication user lookup — the two
  -- operations that legitimately precede a tenant context — can run inside
  -- narrow SECURITY DEFINER functions. It has NOLOGIN: nothing connects as it.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agentic_provisioner') THEN
    CREATE ROLE agentic_provisioner NOLOGIN BYPASSRLS;
  END IF;
END $$;

ALTER ROLE agentic_app NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE agentic_owner NOSUPERUSER NOCREATEROLE;
GRANT agentic_provisioner TO agentic_owner;

SELECT 'run 01_extensions.sql against the application database next' AS next_step;
