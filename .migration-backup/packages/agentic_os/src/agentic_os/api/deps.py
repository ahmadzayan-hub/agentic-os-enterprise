"""FastAPI dependencies: authentication, tenant binding and authorization."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Protocol, cast

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext
from agentic_os.core.db import bind_tenant, get_session_factory
from agentic_os.core.errors import AgenticError, AuthenticationError
from agentic_os.identity.authn import AuthenticatedPrincipal, verify_access_token
from agentic_os.identity.authz import (
    AuthorizationRequest,
    Resource,
    authorize,
)


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedPrincipal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "AUTHENTICATION", "message": "Bearer token required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verify_access_token(authorization.split(" ", 1)[1].strip())
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.to_dict(),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_context(
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> ExecutionContext:
    """Authenticated context with the session's tenant bound for RLS."""
    from agentic_os.core.ids import correlation_id as new_correlation_id
    from agentic_os.identity.authn import session_is_active

    ctx = principal.to_context(correlation_id=request.headers.get("x-correlation-id") or new_correlation_id())
    # Bind the tenant *before* touching any protected table: the sessions table
    # is RLS-scoped, so an unbound lookup would find nothing and every request
    # would look like a revoked session.
    bind_tenant(db, ctx.tenant_id, actor=ctx.actor_id)

    if principal.session_id and not session_is_active(db, principal.session_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "AUTHENTICATION", "message": "session has been revoked"},
        )
    return ctx


CtxDep = Annotated[ExecutionContext, Depends(get_context)]
DbDep = Annotated[Session, Depends(get_db)]


class PermissionDependency(Protocol):
    """A route dependency that carries the permission it enforces.

    The attributes are read back by introspection — the generated API reference
    and the test asserting every mutating route declares a permission both walk
    the route table looking for them. Declaring the shape here replaces two
    `type: ignore[attr-defined]` comments without altering anything at runtime:
    the cast below is erased, so FastAPI still receives the same plain function
    and resolves it exactly as before. Making this a callable class would type
    just as well but would change how FastAPI introspects the dependency, which
    is not a change worth making to the authorization path for tidiness.
    """

    required_permission: str
    resource_type: str

    def __call__(self, ctx: CtxDep) -> ExecutionContext: ...


def require_permission(permission: str, *, resource_type: str = "api") -> PermissionDependency:
    """Dependency factory enforcing one permission through the authz engine."""

    def dependency(ctx: CtxDep) -> ExecutionContext:
        decision = authorize(
            ctx,
            AuthorizationRequest(
                action=permission,
                resource=Resource(resource_type, tenant_id=ctx.tenant_id),
            ),
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "AUTHORIZATION",
                    "message": decision.reason,
                    "failed_stage": decision.failed_stage,
                },
            )
        return ctx

    # Recorded so the route's requirement can be read back by introspection.
    declared = cast(PermissionDependency, dependency)
    declared.required_permission = permission
    declared.resource_type = resource_type
    return declared


def to_http(exc: AgenticError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=exc.to_dict())
