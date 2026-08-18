"""Authentication endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agentic_os.api.deps import CtxDep, DbDep, get_db, get_principal
from agentic_os.core.errors import AgenticError
from agentic_os.identity.authn import (
    AuthenticatedPrincipal,
    authenticate_password,
    issue_access_token,
    revoke_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=512)
    mfa_code: str | None = Field(default=None, max_length=12)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    refresh_token: str
    user: dict


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest, request: Request, db: Annotated[Session, Depends(get_db)]
) -> TokenResponse:
    try:
        principal = authenticate_password(
            db,
            payload.email,
            payload.password,
            mfa_code=payload.mfa_code,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", ""),
        )
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc

    tokens = issue_access_token(principal)
    return TokenResponse(
        **tokens,
        user={
            "user_id": principal.user_id,
            "email": principal.email,
            "display_name": principal.display_name,
            "tenant_id": principal.tenant_id,
            "roles": sorted(principal.roles),
            "permissions": sorted(principal.permissions),
            "clearance": principal.clearance,
            "mfa_satisfied": principal.mfa_satisfied,
        },
    )


@router.get("/me")
def me(ctx: CtxDep) -> dict:
    assert ctx.human is not None
    return {
        "user_id": ctx.human.user_id,
        "email": ctx.human.email,
        "display_name": ctx.human.display_name,
        "tenant_id": ctx.tenant_id,
        "organization_id": ctx.organization_id,
        "roles": sorted(ctx.human.roles),
        "permissions": sorted(ctx.human.permissions),
        "clearance": ctx.human.clearance,
        "mfa_satisfied": ctx.human.mfa_satisfied,
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    ctx: CtxDep,
    db: DbDep,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_principal)],
) -> None:
    revoke_session(db, ctx.tenant_id, principal.session_id)
