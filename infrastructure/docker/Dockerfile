# Single image for every Python surface: API, worker, migrations, evidence and
# DR CLIs. One build means one thing to scan, sign and promote; the command
# chooses the role. Non-root by construction.

FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libpq is needed for psycopg, and pg_dump/pg_restore for the disaster recovery
# exercise. The client must match the server major version — pg_dump 15 refuses
# to dump a 16 server — and Debian bookworm ships 15, so the PGDG repository is
# added to get 16.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && install -d -m 0755 /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 \
 && apt-get purge -y gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so a source change does not re-resolve the world.
COPY pyproject.toml README.md ./
COPY packages/agentic_os/src/agentic_os/__init__.py packages/agentic_os/src/agentic_os/__init__.py
RUN pip install --no-cache-dir ".[parsers]"

COPY packages ./packages
COPY database ./database
COPY evaluations ./evaluations
COPY packages/contracts ./packages/contracts
COPY skills ./skills
COPY models ./models
COPY tools ./tools
COPY policies ./policies
COPY prompts ./prompts
COPY scripts ./scripts
RUN pip install --no-cache-dir --no-deps -e .

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin agentic \
 && mkdir -p /app/.data && chown -R agentic:agentic /app
USER 10001

EXPOSE 8000

# --------------------------------------------------------------------- api
FROM base AS api
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1
CMD ["uvicorn", "agentic_os.api.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]

# ------------------------------------------------------------------ worker
FROM base AS worker
# The worker exposes no port. Liveness is the process itself; progress is
# visible in the metric_samples the worker writes each pass.
CMD ["agentic-worker"]
