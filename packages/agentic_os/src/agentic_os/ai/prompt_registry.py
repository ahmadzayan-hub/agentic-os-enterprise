"""Prompt registry access.

Prompts are production assets. The runtime resolves a prompt by key to the
version marked DEPLOYED, verifies its body hash, and returns the body together
with the version identity so that the run record, the audit entry and any
evaluation all name the exact prompt that executed.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.crypto import content_hash
from agentic_os.core.errors import Conflict, NotFound


@dataclass(frozen=True, slots=True)
class ResolvedPrompt:
    key: str
    version: str
    body: str
    body_hash: str
    owning_agent: str
    deployment_status: str


def resolve(session: Session, tenant_id: str, prompt_key: str) -> ResolvedPrompt:
    row = (
        session.execute(
            text(
                """
            SELECT p.prompt_key, pv.version, pv.body, pv.body_hash,
                   p.owning_agent_key, pv.deployment_status
            FROM prompts p
            JOIN prompt_versions pv ON pv.prompt_id = p.id AND pv.tenant_id = p.tenant_id
            WHERE p.tenant_id = :t AND p.prompt_key = :k AND pv.deployment_status = 'DEPLOYED'
            ORDER BY pv.effective_from DESC NULLS LAST
            LIMIT 1
            """
            ),
            {"t": tenant_id, "k": prompt_key},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound(f"no deployed version of prompt '{prompt_key}'")

    # Integrity check: a body edited in place without a version bump is a
    # governance failure, not something to serve quietly.
    actual = content_hash(row["body"])
    if actual != row["body_hash"]:
        raise Conflict(
            f"prompt '{prompt_key}' v{row['version']} body does not match its recorded hash",
            details={"recorded": row["body_hash"][:16], "actual": actual[:16]},
        )
    return ResolvedPrompt(
        key=row["prompt_key"],
        version=row["version"],
        body=row["body"],
        body_hash=row["body_hash"],
        owning_agent=row["owning_agent_key"],
        deployment_status=row["deployment_status"],
    )


def list_prompts(session: Session, tenant_id: str) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT p.prompt_key, p.purpose, p.owning_agent_key, p.current_version,
                   pv.version, pv.deployment_status, pv.body_hash, pv.evaluation_score,
                   pv.effective_from
            FROM prompts p
            LEFT JOIN prompt_versions pv
              ON pv.prompt_id = p.id AND pv.version = p.current_version
            WHERE p.tenant_id = :t
            ORDER BY p.prompt_key
            """
        ),
        {"t": tenant_id},
    ).mappings()
    return [dict(r) for r in rows]


def publish_version(
    session: Session,
    tenant_id: str,
    prompt_key: str,
    version: str,
    body: str,
    *,
    author_user_id: str | None = None,
    rollback_version: str = "",
) -> ResolvedPrompt:
    """Create a new CANDIDATE version. Deployment is a separate, gated step."""
    prompt = session.execute(
        text("SELECT id FROM prompts WHERE tenant_id = :t AND prompt_key = :k"),
        {"t": tenant_id, "k": prompt_key},
    ).first()
    if prompt is None:
        raise NotFound(f"unknown prompt '{prompt_key}'")

    session.execute(
        text(
            """
            INSERT INTO prompt_versions (tenant_id, prompt_id, version, body, body_hash,
                                         author_user_id, deployment_status, rollback_version)
            VALUES (:t, :p, :v, :b, :h, :a, 'CANDIDATE', :rb)
            """
        ),
        {
            "t": tenant_id,
            "p": prompt.id,
            "v": version,
            "b": body,
            "h": content_hash(body),
            "a": author_user_id,
            "rb": rollback_version,
        },
    )
    return ResolvedPrompt(
        key=prompt_key,
        version=version,
        body=body,
        body_hash=content_hash(body),
        owning_agent="",
        deployment_status="CANDIDATE",
    )


def deploy_version(
    session: Session, tenant_id: str, prompt_key: str, version: str, *, approved_by: str
) -> None:
    """Promote a candidate to DEPLOYED, demoting the incumbent.

    The caller is responsible for having run the regression suite; the CI
    release gate enforces that a prompt change cannot merge without it.
    """
    prompt = session.execute(
        text("SELECT id FROM prompts WHERE tenant_id = :t AND prompt_key = :k"),
        {"t": tenant_id, "k": prompt_key},
    ).first()
    if prompt is None:
        raise NotFound(f"unknown prompt '{prompt_key}'")

    session.execute(
        text(
            "UPDATE prompt_versions SET deployment_status = 'ROLLED_BACK' "
            "WHERE prompt_id = :p AND deployment_status = 'DEPLOYED'"
        ),
        {"p": prompt.id},
    )
    result = session.execute(
        text(
            """
            UPDATE prompt_versions
               SET deployment_status = 'DEPLOYED', approved_by = :by,
                   approved_at = now(), effective_from = now()
             WHERE prompt_id = :p AND version = :v
            """
        ),
        {"p": prompt.id, "v": version, "by": approved_by},
    )
    if result.rowcount == 0:
        raise NotFound(f"prompt '{prompt_key}' has no version {version}")
    session.execute(
        text("UPDATE prompts SET current_version = :v WHERE id = :p"),
        {"p": prompt.id, "v": version},
    )
