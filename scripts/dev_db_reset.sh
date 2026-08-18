#!/usr/bin/env bash
# Recreate the local development database from scratch.
# Requires superuser access to the local PostgreSQL cluster.
set -euo pipefail

DB=${AGENTIC_DB_NAME:-agentic}
PSQL_SUPER=${PSQL_SUPER:-"su postgres -c psql"}

echo "==> dropping and recreating database '$DB'"
$PSQL_SUPER <<SQL
DROP DATABASE IF EXISTS $DB;
SQL
$PSQL_SUPER -f "$(dirname "$0")/../database/bootstrap/00_cluster_bootstrap.sql" >/dev/null
$PSQL_SUPER <<SQL
CREATE DATABASE $DB OWNER agentic_owner;
SQL
su postgres -c "psql -d $DB -f $(cd "$(dirname "$0")/.." && pwd)/database/bootstrap/01_extensions.sql" >/dev/null

echo "==> applying migrations"
"$(dirname "$0")/../.venv/bin/agentic-migrate" up

echo "==> done"
