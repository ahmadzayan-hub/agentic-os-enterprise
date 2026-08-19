"""Database engine, session management and tenant binding.

Tenant isolation is enforced in the database with PostgreSQL Row Level
Security. Application code never adds ``WHERE tenant_id = ...`` by hand for
protected tables; instead every session sets the ``app.tenant_id`` GUC and the
RLS policies do the filtering. Forgetting to bind a tenant therefore returns
zero rows rather than leaking another tenant's data.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from agentic_os.core.config import Settings, get_settings
from agentic_os.core.context import ExecutionContext

_engine: Engine | None = None
_owner_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _build_engine(url: str, settings: Settings) -> Engine:
    engine = create_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        echo=settings.db_echo,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_session_defaults(dbapi_conn: Any, _record: Any) -> None:
        with dbapi_conn.cursor() as cur:
            cur.execute("SET application_name = 'agentic-os'")
            cur.execute("SET statement_timeout = '60s'")
            cur.execute("SET idle_in_transaction_session_timeout = '120s'")

    return engine


def get_engine() -> Engine:
    """Application engine — runs as the least-privilege role subject to RLS."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = _build_engine(settings.database_url, settings)
    return _engine


def get_owner_engine() -> Engine:
    """Owner engine — used only by migrations and RLS provisioning."""
    global _owner_engine
    if _owner_engine is None:
        settings = get_settings()
        _owner_engine = _build_engine(settings.database_owner_url, settings)
    return _owner_engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), expire_on_commit=False, autoflush=False, future=True
        )
    return _session_factory


def dispose_engines() -> None:
    """Drop pooled connections — used between test modules and on shutdown."""
    global _engine, _owner_engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    if _owner_engine is not None:
        _owner_engine.dispose()
    _engine = _owner_engine = None
    _session_factory = None


def bind_tenant(session: Session, tenant_id: str | uuid.UUID | None, *, actor: str = "") -> None:
    """Bind the RLS tenant GUC for this session.

    ``set_config(..., true)`` scopes the setting to the current transaction so
    a pooled connection can never carry one tenant's binding into another
    request.
    """
    session.execute(
        text("SELECT set_config('app.tenant_id', :tenant, true)"),
        {"tenant": str(tenant_id) if tenant_id else ""},
    )
    session.execute(
        text("SELECT set_config('app.actor_id', :actor, true)"),
        {"actor": actor or ""},
    )


def clear_tenant(session: Session) -> None:
    bind_tenant(session, None)


@contextlib.contextmanager
def session_scope(ctx: ExecutionContext | None = None) -> Iterator[Session]:
    """Transactional session bound to the caller's tenant.

    Commits on success, rolls back on any exception.
    """
    factory = get_session_factory()
    session = factory()
    try:
        if ctx is not None:
            bind_tenant(session, ctx.tenant_id, actor=ctx.actor_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextlib.contextmanager
def owner_session_scope() -> Iterator[Session]:
    """Privileged session for migrations and platform-level administration.

    The owner role bypasses no RLS policy either: every protected table
    declares ``FORCE ROW LEVEL SECURITY`` and there is no bypass predicate.
    Platform-wide work iterates tenants and binds each one explicitly.
    """
    factory = sessionmaker(bind=get_owner_engine(), expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextlib.contextmanager
def provisioning_session_scope() -> Iterator[Session]:
    """Session that may write platform-scope rows and cross-tenant tenancy data.

    ``SET ROLE agentic_provisioner`` is the *only* way to obtain RLS bypass in
    this codebase. The role has NOLOGIN, so it is unreachable except by a
    principal that is already a member (the migration owner). Use it for
    catalogue synchronisation, tenant provisioning and platform seeding —
    never for request handling.
    """
    factory = sessionmaker(bind=get_owner_engine(), expire_on_commit=False, future=True)
    session = factory()
    try:
        session.execute(text("SET ROLE agentic_provisioner"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.execute(text("RESET ROLE"))
        session.close()


def healthcheck() -> dict[str, Any]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            version = conn.execute(text("SHOW server_version")).scalar_one()
            has_vector = conn.execute(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one()
        return {"status": "ok", "server_version": version, "pgvector": bool(has_vector)}
    except Exception as exc:  # pragma: no cover - surfaced through /health
        return {"status": "error", "detail": str(exc)}
