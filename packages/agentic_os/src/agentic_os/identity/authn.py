"""Authentication: password login, token issue/verify, sessions, SSO mapping."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt
from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.assurance.audit import AuditEntry, AuditLedger
from agentic_os.core.config import get_settings
from agentic_os.core.context import (
    DataClassification,
    ExecutionContext,
    HumanIdentity,
    as_classification,
)
from agentic_os.core.crypto import hash_password, sha256_hex, verify_password
from agentic_os.core.db import affected_rows, bind_tenant
from agentic_os.core.errors import AuthenticationError, ValidationError
from agentic_os.core.ids import random_token, utcnow
from agentic_os.identity.mfa import user_requires_mfa, verify_totp


@dataclass(slots=True)
class AuthenticatedPrincipal:
    user_id: str
    tenant_id: str
    organization_id: str
    email: str
    display_name: str
    roles: frozenset[str]
    permissions: frozenset[str]
    groups: frozenset[str]
    clearance: DataClassification
    mfa_satisfied: bool
    session_id: str

    def to_human_identity(self) -> HumanIdentity:
        return HumanIdentity(
            user_id=self.user_id,
            email=self.email,
            display_name=self.display_name,
            roles=self.roles,
            permissions=self.permissions,
            groups=self.groups,
            mfa_satisfied=self.mfa_satisfied,
            session_id=self.session_id,
            clearance=self.clearance,
        )

    def to_context(self, **overrides: Any) -> ExecutionContext:
        return ExecutionContext(
            tenant_id=self.tenant_id,
            organization_id=self.organization_id,
            human=self.to_human_identity(),
            environment=get_settings().app_env,
            **overrides,
        )


def _valid_ip(value: str | None) -> str | None:
    """Return the address only if it actually parses.

    The client address arrives from a proxy header or an ASGI transport and is
    not guaranteed to be an address at all. Storing NULL for an unparseable
    value keeps the audit trail honest and stops a malformed header from
    failing an otherwise valid login.
    """
    import ipaddress

    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def load_grants(
    session: Session, tenant_id: str, user_id: str
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return (roles, permissions, groups) for a user.

    Expired role grants are excluded, so a time-boxed elevation lapses without
    any cleanup job running.
    """
    rows = session.execute(
        text(
            """
            SELECT r.slug AS role_slug, rp.permission_id
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            LEFT JOIN role_permissions rp ON rp.role_id = r.id
            WHERE ur.user_id = :uid
              AND ur.tenant_id = :tid
              AND (ur.expires_at IS NULL OR ur.expires_at > now())
            """
        ),
        {"uid": user_id, "tid": tenant_id},
    ).all()
    roles = {r.role_slug for r in rows}
    permissions = {r.permission_id for r in rows if r.permission_id}

    group_rows = session.execute(
        text(
            """
            SELECT g.slug FROM user_groups ug
            JOIN groups g ON g.id = ug.group_id
            WHERE ug.user_id = :uid AND ug.tenant_id = :tid
            """
        ),
        {"uid": user_id, "tid": tenant_id},
    ).all()
    return frozenset(roles), frozenset(permissions), frozenset(g.slug for g in group_rows)


def authenticate_password(
    session: Session,
    email: str,
    password: str,
    *,
    mfa_code: str | None = None,
    ip_address: str | None = None,
    user_agent: str = "",
) -> AuthenticatedPrincipal:
    """Verify credentials and open a session.

    The user lookup runs through ``auth_bootstrap_user`` because login precedes
    tenant binding; the function exposes only the columns authentication needs.
    """
    email = (email or "").strip().lower()
    if not email or not password:
        raise AuthenticationError("Email and password are required")

    row = (
        session.execute(
            text(
                """
            SELECT id, tenant_id, organization_id, password_hash, status,
                   mfa_enrolled, clearance, locked_until, failed_login_count, display_name
            FROM auth_bootstrap_user(:email)
            """
            ),
            {"email": email},
        )
        .mappings()
        .first()
    )

    # Constant-ish work on the unknown-user path so response time does not
    # disclose whether an account exists.
    if row is None:
        verify_password(
            "$argon2id$v=19$m=65536,t=3,p=2$c29tZXNhbHRzb21lc2FsdA$"
            "0000000000000000000000000000000000000000000",
            password,
        )
        raise AuthenticationError("Invalid credentials")

    if row["locked_until"] is not None and row["locked_until"] > utcnow():
        raise AuthenticationError("Account is temporarily locked after repeated failures")
    if row["status"] != "ACTIVE":
        raise AuthenticationError("Account is not active")

    stored = row["password_hash"] or ""
    if not stored or not verify_password(stored, password):
        session.execute(text("SELECT auth_record_login_attempt(:uid, false)"), {"uid": row["id"]})
        _audit_auth(session, str(row["tenant_id"]), str(row["id"]), email, "login.password", "DENIED")
        session.commit()
        raise AuthenticationError("Invalid credentials")

    tenant_id = str(row["tenant_id"])
    bind_tenant(session, tenant_id, actor=str(row["id"]))

    roles, permissions, groups = load_grants(session, tenant_id, str(row["id"]))

    settings = get_settings()
    mfa_required = user_requires_mfa(session, str(row["id"])) or bool(row["mfa_enrolled"])
    mfa_satisfied = verify_totp(session, str(row["id"]), mfa_code) if mfa_required else True
    if mfa_required and not mfa_satisfied:
        _audit_auth(session, tenant_id, str(row["id"]), email, "login.mfa_required", "DENIED")
        session.commit()
        raise AuthenticationError(
            "Multi-factor authentication is required for this account",
            details={"mfa_required": True},
        )

    refresh_token = random_token()
    session_row = session.execute(
        text(
            """
            INSERT INTO sessions (tenant_id, user_id, refresh_token_hash, mfa_satisfied,
                                  ip_address, user_agent, expires_at)
            VALUES (:tid, :uid, :rth, :mfa, CAST(:ip AS inet), :ua, :exp)
            RETURNING id
            """
        ),
        {
            "tid": tenant_id,
            "uid": row["id"],
            "rth": sha256_hex(refresh_token),
            "mfa": mfa_satisfied,
            "ip": _valid_ip(ip_address),
            "ua": user_agent[:512],
            "exp": utcnow() + timedelta(seconds=settings.refresh_token_ttl_seconds),
        },
    ).one()

    session.execute(text("SELECT auth_record_login_attempt(:uid, true)"), {"uid": row["id"]})

    principal = AuthenticatedPrincipal(
        user_id=str(row["id"]),
        tenant_id=tenant_id,
        organization_id=str(row["organization_id"]),
        email=email,
        display_name=row["display_name"] or email,
        roles=roles,
        permissions=permissions,
        groups=groups,
        clearance=as_classification(row["clearance"]),
        mfa_satisfied=mfa_satisfied,
        session_id=str(session_row.id),
    )
    principal_refresh_tokens[principal.session_id] = refresh_token
    _audit_auth(session, tenant_id, principal.user_id, email, "login.password", "SUCCESS")
    return principal


#: Refresh tokens are returned to the caller once, at issue time. Only the hash
#: is persisted, so this in-process map is the single hand-off point.
principal_refresh_tokens: dict[str, str] = {}


def issue_access_token(principal: AuthenticatedPrincipal) -> dict[str, Any]:
    settings = get_settings()
    now = int(time.time())
    claims = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": principal.user_id,
        "sid": principal.session_id,
        "tid": principal.tenant_id,
        "oid": principal.organization_id,
        "email": principal.email,
        "name": principal.display_name,
        "roles": sorted(principal.roles),
        "perms": sorted(principal.permissions),
        "groups": sorted(principal.groups),
        "clr": principal.clearance,
        "mfa": principal.mfa_satisfied,
        "iat": now,
        "nbf": now,
        "exp": now + settings.access_token_ttl_seconds,
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": settings.access_token_ttl_seconds,
        "refresh_token": principal_refresh_tokens.pop(principal.session_id, ""),
    }


def verify_access_token(token: str) -> AuthenticatedPrincipal:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "tid"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid access token") from exc

    return AuthenticatedPrincipal(
        user_id=claims["sub"],
        tenant_id=claims["tid"],
        organization_id=claims.get("oid", ""),
        email=claims.get("email", ""),
        display_name=claims.get("name", ""),
        roles=frozenset(claims.get("roles", [])),
        permissions=frozenset(claims.get("perms", [])),
        groups=frozenset(claims.get("groups", [])),
        clearance=as_classification(claims.get("clr", "INTERNAL")),
        mfa_satisfied=bool(claims.get("mfa", False)),
        session_id=claims.get("sid", ""),
    )


def revoke_session(session: Session, tenant_id: str, session_id: str) -> bool:
    result = session.execute(
        text(
            "UPDATE sessions SET revoked_at = now() "
            "WHERE id = :sid AND tenant_id = :tid AND revoked_at IS NULL"
        ),
        {"sid": session_id, "tid": tenant_id},
    )
    return affected_rows(result) > 0


def session_is_active(session: Session, session_id: str) -> bool:
    row = session.execute(
        text("SELECT 1 FROM sessions WHERE id = :sid AND revoked_at IS NULL AND expires_at > now()"),
        {"sid": session_id},
    ).first()
    return row is not None


def set_user_password(session: Session, user_id: str, password: str) -> None:
    settings = get_settings()
    if len(password) < settings.password_min_length:
        raise ValidationError(f"Password must be at least {settings.password_min_length} characters")
    session.execute(
        text("UPDATE users SET password_hash = :h, updated_at = now() WHERE id = :uid"),
        {"h": hash_password(password), "uid": user_id},
    )


def _audit_auth(
    session: Session, tenant_id: str, user_id: str, email: str, action: str, outcome: str
) -> None:
    # The tenant GUC is transaction-scoped, so re-bind before writing: this
    # helper is also reached on paths that have just committed.
    bind_tenant(session, tenant_id, actor=user_id)
    ctx = ExecutionContext(
        tenant_id=tenant_id,
        organization_id="",
        human=HumanIdentity(user_id=user_id, email=email),
    )
    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="AUTH",
            action=action,
            outcome=outcome,  # type: ignore[arg-type]
            resource_type="user",
            resource_id=user_id,
            payload={"email": email},
        ),
    )
