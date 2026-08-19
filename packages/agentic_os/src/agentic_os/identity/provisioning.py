"""Tenant, role and user provisioning."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.crypto import hash_password
from agentic_os.core.db import bind_tenant
from agentic_os.core.errors import Conflict, ValidationError
from agentic_os.identity.permissions import CATALOGUE, SYSTEM_ROLES


def sync_permission_catalogue(session: Session) -> int:
    """Upsert the permission catalogue. Runs as the owner role."""
    for perm in CATALOGUE:
        session.execute(
            text(
                """
                INSERT INTO permissions (id, description, resource, action, risk)
                VALUES (:id, :d, :r, :a, CAST(:risk AS risk_class))
                ON CONFLICT (id) DO UPDATE
                  SET description = EXCLUDED.description,
                      resource = EXCLUDED.resource,
                      action = EXCLUDED.action,
                      risk = EXCLUDED.risk
                """
            ),
            {
                "id": perm.id,
                "d": perm.description,
                "r": perm.resource,
                "a": perm.action,
                "risk": perm.risk,
            },
        )
    return len(CATALOGUE)


def sync_system_roles(session: Session) -> int:
    """Create or refresh the tenant-independent system roles."""
    for role in SYSTEM_ROLES:
        row = session.execute(
            text(
                """
                INSERT INTO roles (tenant_id, slug, name, description, is_system,
                                   requires_mfa, max_autonomy)
                VALUES (NULL, :slug, :name, :desc, true, :mfa,
                        CAST(:autonomy AS autonomy_level))
                ON CONFLICT (slug) WHERE tenant_id IS NULL
                DO UPDATE SET name = EXCLUDED.name,
                              description = EXCLUDED.description,
                              requires_mfa = EXCLUDED.requires_mfa,
                              max_autonomy = EXCLUDED.max_autonomy
                RETURNING id
                """
            ),
            {
                "slug": role.slug,
                "name": role.name,
                "desc": role.description,
                "mfa": role.requires_mfa,
                "autonomy": role.max_autonomy,
            },
        ).one()
        session.execute(text("DELETE FROM role_permissions WHERE role_id = :rid"), {"rid": row.id})
        for pid in role.permissions:
            session.execute(
                text(
                    "INSERT INTO role_permissions (role_id, permission_id) "
                    "VALUES (:rid, :pid) ON CONFLICT DO NOTHING"
                ),
                {"rid": row.id, "pid": pid},
            )
    return len(SYSTEM_ROLES)


def provision_tenant(
    session: Session,
    org_slug: str,
    org_name: str,
    tenant_slug: str,
    tenant_name: str,
    region: str = "global",
) -> dict[str, str]:
    """Create (or fetch) an organization and tenant. Idempotent."""
    row = session.execute(
        text(
            "SELECT out_organization_id, out_tenant_id FROM platform_provision_tenant(:os, :on, :ts, :tn, :r)"
        ),
        {"os": org_slug, "on": org_name, "ts": tenant_slug, "tn": tenant_name, "r": region},
    ).one()
    return {
        "organization_id": str(row.out_organization_id),
        "tenant_id": str(row.out_tenant_id),
    }


def create_user(
    session: Session,
    *,
    tenant_id: str,
    organization_id: str,
    email: str,
    display_name: str,
    password: str | None = None,
    roles: list[str] | None = None,
    clearance: str = "INTERNAL",
    mfa_enrolled: bool = False,
    attributes: dict[str, Any] | None = None,
) -> str:
    """Create a user and assign roles. Requires a bound tenant session."""
    import json

    email = (email or "").strip().lower()
    if "@" not in email:
        raise ValidationError("A valid email address is required")

    bind_tenant(session, tenant_id)
    existing = session.execute(
        text("SELECT id FROM users WHERE tenant_id = :t AND email = :e"),
        {"t": tenant_id, "e": email},
    ).first()
    if existing is not None:
        raise Conflict(f"user {email} already exists in this tenant")

    row = session.execute(
        text(
            """
            INSERT INTO users (tenant_id, organization_id, email, display_name,
                               password_hash, clearance, mfa_enrolled, attributes)
            VALUES (:t, :o, :e, :n, :p, CAST(:c AS data_classification), :mfa,
                    CAST(:attrs AS jsonb))
            RETURNING id
            """
        ),
        {
            "t": tenant_id,
            "o": organization_id,
            "e": email,
            "n": display_name,
            "p": hash_password(password) if password else None,
            "c": clearance,
            "mfa": mfa_enrolled,
            "attrs": json.dumps(attributes or {}),
        },
    ).one()
    user_id = str(row.id)

    for slug in roles or []:
        assign_role(session, tenant_id=tenant_id, user_id=user_id, role_slug=slug)
    return user_id


def assign_role(
    session: Session,
    *,
    tenant_id: str,
    user_id: str,
    role_slug: str,
    granted_by: str | None = None,
    expires_at: Any = None,
) -> None:
    role = session.execute(
        text(
            "SELECT id FROM roles WHERE slug = :s AND (tenant_id IS NULL OR tenant_id = :t) "
            "ORDER BY tenant_id NULLS LAST LIMIT 1"
        ),
        {"s": role_slug, "t": tenant_id},
    ).first()
    if role is None:
        raise ValidationError(f"unknown role '{role_slug}'")
    session.execute(
        text(
            """
            INSERT INTO user_roles (user_id, role_id, tenant_id, granted_by, expires_at)
            VALUES (:u, :r, :t, :g, :e)
            ON CONFLICT (user_id, role_id) DO UPDATE
              SET expires_at = EXCLUDED.expires_at, granted_at = now()
            """
        ),
        {"u": user_id, "r": role.id, "t": tenant_id, "g": granted_by, "e": expires_at},
    )
