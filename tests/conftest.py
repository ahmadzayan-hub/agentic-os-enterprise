"""Shared pytest fixtures and the service gates the suites depend on.

Integration tests run against a real PostgreSQL instance with pgvector. There
is no in-memory substitute: row level security, the append-only ledger triggers
and the vector index are the things under test, and a fake would not have them.

Set AGENTIC_DATABASE_URL / AGENTIC_DATABASE_OWNER_URL to point at a scratch
database. ``make db-reset`` and the CI service container both provide one.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

os.environ.setdefault("AGENTIC_APP_ENV", "test")
os.environ.setdefault("AGENTIC_JWT_SECRET", "test-only-signing-key-not-for-production-0001")

from agentic_os.core.db import (  # noqa: E402
    bind_tenant,
    get_session_factory,
    provisioning_session_scope,
)
from agentic_os.core.seed import DEMO_PASSWORD, seed_all  # noqa: E402

# ------------------------------------------------------------- service gates
#
# A skipped test reads as a passing test. That is how a suite reports green
# while the thing it was meant to prove never ran: if PostgreSQL became
# unreachable partway through a CI job, every integration test would skip and
# the job would still succeed, because "0 failed" is all the exit code says.
#
# AGENTIC_REQUIRE_SERVICES names the services that must genuinely be present.
# For those, absence is a failure rather than a skip. CI sets it, because CI
# provisions the services and their absence is a defect. A developer without a
# local Redis sets nothing and still gets a polite skip.


def _database_available() -> bool:
    try:
        from agentic_os.core.db import get_engine

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _redis_available() -> bool:
    try:
        import redis

        redis.Redis.from_url(REDIS_URL).ping()
        return True
    except Exception:
        return False


def _dr_identity_available() -> bool:
    from agentic_os.core.config import get_settings

    return bool(get_settings().dr_admin_url)


REDIS_URL = os.environ.get("AGENTIC_REDIS_URL", "redis://127.0.0.1:6379/15")

SERVICES: dict[str, tuple[Any, str]] = {
    "db": (
        _database_available,
        "PostgreSQL is not reachable; set AGENTIC_DATABASE_URL to run integration tests",
    ),
    "redis": (
        _redis_available,
        f"no Redis at {REDIS_URL}; set AGENTIC_REDIS_URL to run the shared limiter tests",
    ),
    "dr": (
        _dr_identity_available,
        "AGENTIC_DR_ADMIN_URL is not set; a real restore cannot be performed",
    ),
}


def required_services() -> frozenset[str]:
    """Services whose absence must fail the run rather than skip it."""
    raw = os.environ.get("AGENTIC_REQUIRE_SERVICES", "").strip().lower()
    if raw in {"1", "true", "yes", "all"}:
        return frozenset(SERVICES)
    names = frozenset(part.strip() for part in raw.split(",") if part.strip())
    unknown = names - set(SERVICES)
    if unknown:
        raise pytest.UsageError(
            f"AGENTIC_REQUIRE_SERVICES names unknown services: {sorted(unknown)}. "
            f"Known services: {sorted(SERVICES)}"
        )
    return names


@functools.cache
def service_available(name: str) -> bool:
    """Probe a service once per session; the result is cached."""
    return bool(SERVICES[name][0]())


def pytest_configure(config: pytest.Config) -> None:
    """Validate the requirement list before a single test runs.

    Eagerly, not on first use: a misspelt service name would otherwise stay
    invisible for as long as the services happened to be up, and only surface
    on the day one of them was down — the one day the gate had to work.
    """
    required_services()


def pytest_runtest_setup(item: pytest.Item) -> None:
    for mark in item.iter_markers(name="requires_service"):
        name = str(mark.args[0])
        if service_available(name):
            continue
        reason = SERVICES[name][1]
        if name in required_services():
            pytest.fail(
                f"AGENTIC_REQUIRE_SERVICES requires {name!r}, but it is unavailable: "
                f"{reason}. This run cannot produce evidence for these tests, so it "
                f"fails rather than reporting a green skip.",
                pytrace=False,
            )
        pytest.skip(reason)


requires_db = pytest.mark.requires_service("db")
requires_redis = pytest.mark.requires_service("redis")
requires_dr_identity = pytest.mark.requires_service("dr")


@pytest.fixture(scope="session")
def seeded() -> dict[str, Any]:
    """Ensure migrations are applied and the demo tenants exist."""
    from agentic_os.core.migrate import migrate

    migrate()
    summary = seed_all(include_domain=True)
    return summary


@pytest.fixture(scope="session")
def tenant_id(seeded: dict) -> str:
    return seeded["identity"]["primary"]["tenant_id"]


@pytest.fixture(scope="session")
def organization_id(seeded: dict) -> str:
    return seeded["identity"]["primary"]["organization_id"]


@pytest.fixture(scope="session")
def other_tenant_id(seeded: dict) -> str:
    return seeded["identity"]["secondary"]["tenant_id"]


@pytest.fixture()
def db(tenant_id: str) -> Iterator[Session]:
    """Application-role session bound to the primary tenant.

    Rolls back at the end so tests never leave state behind — except audit
    ledger rows, which are append-only by design.
    """
    session = get_session_factory()()
    bind_tenant(session, tenant_id, actor="test")
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def db_other(other_tenant_id: str) -> Iterator[Session]:
    """Application-role session bound to the *second* tenant."""
    session = get_session_factory()()
    bind_tenant(session, other_tenant_id, actor="test")
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def db_unbound() -> Iterator[Session]:
    """Session with no tenant binding. Should see nothing."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def provisioning_db() -> Iterator[Session]:
    with provisioning_session_scope() as session:
        yield session


@pytest.fixture()
def principal(db: Session, tenant_id: str):
    """An authenticated operator principal in the primary tenant."""
    from agentic_os.identity.authn import authenticate_password

    session = get_session_factory()()
    try:
        p = authenticate_password(session, "systems.lead@rta.example", DEMO_PASSWORD)
        session.commit()
        return p
    finally:
        session.close()


@pytest.fixture()
def ctx(principal):
    return principal.to_context()


@pytest.fixture()
def demo_password() -> str:
    return DEMO_PASSWORD
