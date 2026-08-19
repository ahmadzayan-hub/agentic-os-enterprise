"""Registry surfaces: agents, skills, models, prompts, tools, connectors, MCP."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from agentic_os.ai import prompt_registry
from agentic_os.api.deps import CtxDep, DbDep, require_permission
from agentic_os.api.serialization import jsonify
from agentic_os.api.serialization import rows as json_rows
from agentic_os.core.errors import AgenticError
from agentic_os.tools import mcp

router = APIRouter(tags=["catalog"])


@router.get("/agents", dependencies=[Depends(require_permission("agents:read", resource_type="agent"))])
def list_agents(ctx: CtxDep, db: DbDep) -> dict:
    rows = db.execute(
        text(
            """
            SELECT a.agent_key, a.name, a.description, a.owner_team, a.business_purpose,
                   a.risk_class, a.max_autonomy, a.status, a.current_version,
                   av.contract_hash, av.evaluation_score,
                   ac.allowed_models, ac.allowed_tools, ac.allowed_skills,
                   ac.permitted_domains, ac.prohibited_domains, ac.max_classification,
                   ac.token_budget, ac.cost_budget_usd, ac.max_runtime_seconds,
                   ac.max_tool_calls, ac.slo_success_rate, ac.slo_p95_latency_ms,
                   ac.requires_citations, ac.min_evaluation_score,
                   (SELECT count(*) FROM runs r
                     WHERE r.tenant_id = a.tenant_id AND r.owner_agent_key = a.agent_key) AS run_count
            FROM agents a
            JOIN agent_versions av ON av.agent_id = a.id AND av.version = a.current_version
            LEFT JOIN agent_contracts ac ON ac.agent_version_id = av.id
            WHERE a.tenant_id = :t AND a.deleted_at IS NULL
            ORDER BY a.agent_key
            """
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"agents": json_rows(rows)}


@router.get(
    "/agents/{agent_key}",
    dependencies=[Depends(require_permission("agents:read", resource_type="agent"))],
)
def agent_detail(agent_key: str, ctx: CtxDep, db: DbDep) -> dict:
    row = (
        db.execute(
            text(
                "SELECT a.agent_key, a.name, a.owner_team, a.risk_class, a.max_autonomy, a.status, "
                "a.current_version, av.contract, av.contract_hash, av.published_at "
                "FROM agents a JOIN agent_versions av "
                "  ON av.agent_id = a.id AND av.version = a.current_version "
                "WHERE a.tenant_id = :t AND a.agent_key = :k"
            ),
            {"t": ctx.tenant_id, "k": agent_key},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "message": agent_key})

    recent = db.execute(
        text(
            "SELECT id, objective, status, risk_class, cost_usd, created_at FROM runs "
            "WHERE tenant_id = :t AND owner_agent_key = :k ORDER BY created_at DESC LIMIT 20"
        ),
        {"t": ctx.tenant_id, "k": agent_key},
    ).mappings()
    evaluations = db.execute(
        text(
            "SELECT suite_key, score, threshold, passed, case_count, created_at FROM evaluations "
            "WHERE tenant_id = :t AND target_type = 'AGENT' AND target_key = :k "
            "ORDER BY created_at DESC LIMIT 20"
        ),
        {"t": ctx.tenant_id, "k": agent_key},
    ).mappings()
    return {
        "agent": dict(row),
        "recent_runs": json_rows(recent),
        "evaluations": json_rows(evaluations),
    }


@router.get("/skills", dependencies=[Depends(require_permission("skills:read", resource_type="skill"))])
def list_skills(ctx: CtxDep, db: DbDep) -> dict:
    rows = db.execute(
        text(
            "SELECT s.skill_key, s.name, s.description, s.owner_team, s.execution_mode, "
            "s.risk_class, s.status, s.current_version, sv.input_schema, sv.output_schema, "
            "sv.required_tools, sv.evaluation_threshold FROM skills s "
            "LEFT JOIN skill_versions sv ON sv.skill_id = s.id AND sv.version = s.current_version "
            "WHERE s.tenant_id = :t ORDER BY s.skill_key"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"skills": json_rows(rows)}


@router.get("/models", dependencies=[Depends(require_permission("models:read", resource_type="model"))])
def list_models(ctx: CtxDep, db: DbDep) -> dict:
    rows = db.execute(
        text(
            "SELECT model_key, provider, deployment, owner_team, capabilities, "
            "max_classification, context_window, input_cost_per_1k, output_cost_per_1k, "
            "p95_latency_ms, evaluation_score, known_limitations, residency, approval_state, "
            "status, effective_from, retirement_date FROM models "
            "WHERE tenant_id = :t ORDER BY model_key"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    usage = db.execute(
        text(
            "SELECT model_key, count(*) AS calls, sum(input_tokens + output_tokens) AS tokens, "
            "sum(cost_usd) AS cost FROM cost_records "
            "WHERE tenant_id = :t AND category = 'MODEL' GROUP BY model_key"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"models": json_rows(rows), "usage": json_rows(usage)}


@router.get("/prompts", dependencies=[Depends(require_permission("prompts:read", resource_type="prompt"))])
def list_prompts(ctx: CtxDep, db: DbDep) -> dict:
    return jsonify({"prompts": prompt_registry.list_prompts(db, ctx.tenant_id)})


@router.get(
    "/prompts/{prompt_key}",
    dependencies=[Depends(require_permission("prompts:read", resource_type="prompt"))],
)
def prompt_detail(prompt_key: str, ctx: CtxDep, db: DbDep) -> dict:
    try:
        resolved = prompt_registry.resolve(db, ctx.tenant_id, prompt_key)
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    versions = db.execute(
        text(
            "SELECT pv.version, pv.body_hash, pv.deployment_status, pv.evaluation_score, "
            "pv.effective_from, pv.rollback_version, pv.created_at "
            "FROM prompt_versions pv JOIN prompts p ON p.id = pv.prompt_id "
            "WHERE p.tenant_id = :t AND p.prompt_key = :k ORDER BY pv.created_at DESC"
        ),
        {"t": ctx.tenant_id, "k": prompt_key},
    ).mappings()
    return {
        "prompt": {
            "key": resolved.key,
            "version": resolved.version,
            "body": resolved.body,
            "body_hash": resolved.body_hash,
            "owning_agent": resolved.owning_agent,
            "deployment_status": resolved.deployment_status,
        },
        "versions": json_rows(versions),
    }


@router.get("/tools", dependencies=[Depends(require_permission("tools:read", resource_type="tool"))])
def list_tools(ctx: CtxDep, db: DbDep) -> dict:
    rows = db.execute(
        text(
            "SELECT tool_key, name, description, owner_team, kind, connector_key, scopes, "
            "risk_class, min_autonomy, side_effect, reversibility, max_classification, "
            "rate_limit_per_minute, timeout_seconds, requires_approval, verification_mode, "
            "status, implementation_status, parameter_schema FROM tools "
            "WHERE tenant_id = :t ORDER BY tool_key"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"tools": json_rows(rows)}


@router.get("/tools/calls", dependencies=[Depends(require_permission("tools:read", resource_type="tool"))])
def tool_calls(ctx: CtxDep, db: DbDep, limit: int = 100) -> dict:
    rows = db.execute(
        text(
            "SELECT tool_key, agent_key, gateway_decision, denial_stage, denial_reason, "
            "verification_status, latency_ms, run_id, created_at FROM tool_calls "
            "WHERE tenant_id = :t ORDER BY created_at DESC LIMIT :l"
        ),
        {"t": ctx.tenant_id, "l": min(limit, 500)},
    ).mappings()
    return {"tool_calls": json_rows(rows)}


@router.get(
    "/connectors",
    dependencies=[Depends(require_permission("connectors:read", resource_type="connector"))],
)
def list_connectors(ctx: CtxDep, db: DbDep) -> dict:
    rows = db.execute(
        text(
            "SELECT c.connector_key, c.name, c.provider, c.base_url, c.auth_method, "
            "c.network_destinations, c.data_classification, c.owner_team, c.status, "
            "c.last_security_review, "
            "(SELECT count(*) FROM connector_credentials cc WHERE cc.connector_id = c.id) "
            "  AS credential_count "
            "FROM connectors c WHERE c.tenant_id = :t ORDER BY c.connector_key"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"connectors": json_rows(rows)}


@router.get("/mcp", dependencies=[Depends(require_permission("mcp:read", resource_type="mcp"))])
def list_mcp_servers(ctx: CtxDep, db: DbDep) -> dict:
    return jsonify({"servers": mcp.list_servers(db, ctx)})


class McpClassifyRequest(BaseModel):
    trust_class: str = Field(
        pattern="^(TRUSTED_INTERNAL|APPROVED_EXTERNAL|EXPERIMENTAL|DISABLED|QUARANTINED)$"
    )
    reason: str = Field(min_length=5, max_length=500)


@router.post(
    "/mcp/{server_key}/classify",
    dependencies=[Depends(require_permission("mcp:write", resource_type="mcp"))],
)
def classify_mcp_server(server_key: str, payload: McpClassifyRequest, ctx: CtxDep, db: DbDep) -> dict:
    try:
        mcp.classify_server(db, ctx, server_key, payload.trust_class, reason=payload.reason)
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    return {"server_key": server_key, "trust_class": payload.trust_class}
