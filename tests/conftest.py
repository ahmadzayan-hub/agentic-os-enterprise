"""Shared pytest fixtures.

Integration tests run against a real PostgreSQL instance with pgvector. There
is no in-memory substitute: row level security, the append-only ledger triggers
and the vector index are the things under test, and a fake would not have them.

Set AGENTIC_DATABASE_URL / AGENTIC_DATABASE_OWNER_URL to point at a scratch
database. ``make db-reset`` and the CI service container both provide one.
"""

from __future__ import annotations

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


def _database_available() -> bool:
    try:
        from agentic_os.core.db import get_engine

        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _database_available(),
    reason="PostgreSQL is not reachable; set AGENTIC_DATABASE_URL to run integration tests",
)


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
