"""Development and demo seeding.

Seeding is idempotent: running it repeatedly converges on the same state. The
demo tenant models a rail maintenance department (systems, electromechanical,
rolling stock and infrastructure sections), which gives the agents, policies and
knowledge corpus a coherent domain rather than lorem ipsum.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from agentic_os.core.db import bind_tenant, provisioning_session_scope
from agentic_os.identity.permissions import validate_catalogue
from agentic_os.identity.provisioning import (
    assign_role,
    create_user,
    provision_tenant,
    sync_permission_catalogue,
    sync_system_roles,
)

DEMO_ORG_SLUG = "rta"
DEMO_ORG_NAME = "Roads and Transport Authority"
DEMO_TENANT_SLUG = "rail-maintenance"
DEMO_TENANT_NAME = "Rail Maintenance Department"

#: Second tenant used by tenant-isolation tests and by the UI tenant switcher.
SECOND_TENANT_SLUG = "bus-operations"
SECOND_TENANT_NAME = "Bus Operations Department"

#: Development-only credential for the seeded demo users. It exists so a
#: fresh checkout can be signed into; production tenants are provisioned
#: without it and the seed refuses to run outside development.
DEMO_PASSWORD = "AgenticOS-Demo-2026!"  # noqa: S105

DEMO_USERS: tuple[dict, ...] = (
    {
        "email": "director@rta.example",
        "display_name": "Department Director",
        "roles": ["executive", "approver"],
        "clearance": "RESTRICTED",
        "attributes": {"section": "department", "title": "Director"},
    },
    {
        "email": "chief.engineer@rta.example",
        "display_name": "Chief Engineer",
        "roles": ["operator", "approver", "department_manager"],
        "clearance": "CONFIDENTIAL",
        "attributes": {"section": "department", "title": "Chief Engineer"},
    },
    {
        "email": "systems.lead@rta.example",
        "display_name": "Systems Section Lead",
        "roles": ["operator", "section_lead"],
        "clearance": "CONFIDENTIAL",
        "attributes": {"section": "systems"},
    },
    {
        "email": "rollingstock.lead@rta.example",
        "display_name": "Rolling Stock Section Lead",
        "roles": ["operator", "section_lead"],
        "clearance": "CONFIDENTIAL",
        "attributes": {"section": "rolling_stock"},
    },
    {
        "email": "field.engineer@rta.example",
        "display_name": "Field Maintenance Engineer",
        "roles": ["engineer"],
        "clearance": "INTERNAL",
        "attributes": {"section": "systems"},
    },
    {
        "email": "analyst@rta.example",
        "display_name": "Reliability Analyst",
        "roles": ["analyst"],
        "clearance": "INTERNAL",
        "attributes": {"section": "department"},
    },
    {
        "email": "builder@rta.example",
        "display_name": "Platform Builder",
        "roles": ["builder"],
        "clearance": "INTERNAL",
        "attributes": {"section": "platform"},
    },
    {
        "email": "auditor@rta.example",
        "display_name": "Internal Auditor",
        "roles": ["auditor"],
        "clearance": "RESTRICTED",
        "attributes": {"section": "assurance"},
    },
    {
        "email": "security@rta.example",
        "display_name": "Security Administrator",
        "roles": ["security_admin"],
        "clearance": "RESTRICTED",
        "attributes": {"section": "security"},
    },
    {
        "email": "governance@rta.example",
        "display_name": "Governance Administrator",
        "roles": ["governance_admin"],
        "clearance": "RESTRICTED",
        "attributes": {"section": "assurance"},
    },
    {
        "email": "admin@rta.example",
        "display_name": "Platform Administrator",
        "roles": ["platform_admin"],
        "clearance": "RESTRICTED",
        "attributes": {"section": "platform"},
    },
)


def seed_identity(session) -> dict:
    problems = validate_catalogue()
    if problems:
        raise RuntimeError("permission catalogue is invalid: " + "; ".join(problems))

    sync_permission_catalogue(session)
    sync_system_roles(session)

    primary = provision_tenant(
        session, DEMO_ORG_SLUG, DEMO_ORG_NAME, DEMO_TENANT_SLUG, DEMO_TENANT_NAME, "ae-dxb"
    )
    secondary = provision_tenant(
        session, DEMO_ORG_SLUG, DEMO_ORG_NAME, SECOND_TENANT_SLUG, SECOND_TENANT_NAME, "ae-dxb"
    )

    created = 0
    for spec in DEMO_USERS:
        existing = session.execute(
            text("SELECT id FROM users WHERE tenant_id = :t AND email = :e"),
            {"t": primary["tenant_id"], "e": spec["email"]},
        ).first()
        if existing is not None:
            for slug in spec["roles"]:
                assign_role(
                    session,
                    tenant_id=primary["tenant_id"],
                    user_id=str(existing.id),
                    role_slug=slug,
                )
            continue
        create_user(
            session,
            tenant_id=primary["tenant_id"],
            organization_id=primary["organization_id"],
            email=spec["email"],
            display_name=spec["display_name"],
            password=DEMO_PASSWORD,
            roles=spec["roles"],
            clearance=spec["clearance"],
            attributes=spec["attributes"],
        )
        created += 1

    # A single operator in the second tenant, so isolation tests have a real
    # counterparty rather than an empty tenant.
    other = session.execute(
        text("SELECT id FROM users WHERE tenant_id = :t AND email = :e"),
        {"t": secondary["tenant_id"], "e": "operator@bus.example"},
    ).first()
    if other is None:
        create_user(
            session,
            tenant_id=secondary["tenant_id"],
            organization_id=secondary["organization_id"],
            email="operator@bus.example",
            display_name="Bus Operations Operator",
            password=DEMO_PASSWORD,
            roles=["operator"],
            clearance="INTERNAL",
        )
        created += 1

    bind_tenant(session, primary["tenant_id"])
    enrolled = _enrol_privileged_users(session, primary["tenant_id"])

    return {
        "primary": primary,
        "secondary": secondary,
        "users_created": created,
        "mfa_enrolled": len(enrolled),
        "mfa_secrets": enrolled,
    }


def _enrol_privileged_users(session, tenant_id: str) -> dict[str, str]:
    """Enrol every user holding an MFA-required role.

    The generated secrets are returned so that a development operator (and the
    integration tests) can compute a valid code. Nothing is printed by default
    and nothing is stored in clear: the database holds only a KMS envelope.
    """
    from agentic_os.identity.mfa import enrol_totp, user_requires_mfa

    rows = session.execute(
        text("SELECT id, email FROM users WHERE tenant_id = :t AND deleted_at IS NULL"),
        {"t": tenant_id},
    ).all()
    secrets: dict[str, str] = {}
    for row in rows:
        if not user_requires_mfa(session, str(row.id)):
            continue
        already = session.execute(
            text("SELECT 1 FROM user_mfa WHERE user_id = :u AND secret_ciphertext <> ''"),
            {"u": row.id},
        ).first()
        if already is not None:
            continue
        enrolment = enrol_totp(session, tenant_id=tenant_id, user_id=str(row.id), account=row.email)
        secrets[row.email] = enrolment.secret
    return secrets


def seed_all(*, include_domain: bool = True) -> dict:
    """Seed identity and, optionally, the registries and demo corpus."""
    summary: dict = {}
    with provisioning_session_scope() as session:
        summary["identity"] = seed_identity(session)

    if include_domain:
        from agentic_os.core.seed_domain import seed_domain

        summary["domain"] = seed_domain(
            summary["identity"]["primary"]["tenant_id"],
            summary["identity"]["primary"]["organization_id"],
        )

        from agentic_os.core.seed_decisions import seed_decisions

        with provisioning_session_scope() as session:
            summary["decisions"] = seed_decisions(session, summary["identity"]["primary"]["tenant_id"])
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the Agentic OS database")
    parser.add_argument("--identity-only", action="store_true")
    args = parser.parse_args(argv)

    summary = seed_all(include_domain=not args.identity_only)
    identity = summary["identity"]
    print(f"organization : {identity['primary']['organization_id']}")
    print(f"tenant       : {identity['primary']['tenant_id']} ({DEMO_TENANT_SLUG})")
    print(f"tenant (2)   : {identity['secondary']['tenant_id']} ({SECOND_TENANT_SLUG})")
    print(f"users created: {identity['users_created']}")
    if identity.get("mfa_secrets"):
        print("\nMFA enrolments created (development only - store these securely):")
        for email, secret in sorted(identity["mfa_secrets"].items()):
            print(f"  {email:<34} {secret}")
    if "domain" in summary:
        for key, value in summary["domain"].items():
            print(f"{key:<13}: {value}")
    print(f"\ndemo password: {DEMO_PASSWORD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
