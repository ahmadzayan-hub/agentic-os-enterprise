"""Synchronise declarative registries into the database and seed demo data.

Registry YAML in the repository is the source of truth; the database holds the
runtime projection plus the operational history (versions, evaluations,
evidence). Synchronisation is idempotent and content-addressed: a registry
entry whose hash is unchanged produces no new version row.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.crypto import content_hash
from agentic_os.core.db import bind_tenant, provisioning_session_scope
from agentic_os.core.registry import PROMPTS_DIR, load_registries


def _j(value: Any) -> str:
    return json.dumps(value, default=str)


# ---------------------------------------------------------------------------
# Registry synchronisation
# ---------------------------------------------------------------------------
def sync_agents(session: Session, tenant_id: str) -> int:
    registries = load_registries()
    for agent_id, contract in registries.agents.items():
        meta = contract["agent"]
        limits = contract["limits"]
        evaluation = contract["requirements"]["evaluation"]
        chash = content_hash(contract)

        agent_row = session.execute(
            text(
                """
                INSERT INTO agents (tenant_id, agent_key, name, description, owner_team,
                                    business_purpose, risk_class, max_autonomy, status,
                                    current_version)
                VALUES (:t, :k, :n, :d, :o, :bp, CAST(:rc AS risk_class),
                        CAST(:ma AS autonomy_level), CAST(:st AS lifecycle_status), :v)
                ON CONFLICT (tenant_id, agent_key) DO UPDATE
                  SET name = EXCLUDED.name,
                      description = EXCLUDED.description,
                      owner_team = EXCLUDED.owner_team,
                      business_purpose = EXCLUDED.business_purpose,
                      risk_class = EXCLUDED.risk_class,
                      max_autonomy = EXCLUDED.max_autonomy,
                      status = EXCLUDED.status,
                      current_version = EXCLUDED.current_version,
                      updated_at = now()
                RETURNING id
                """
            ),
            {
                "t": tenant_id,
                "k": agent_id,
                "n": meta.get("name", agent_id),
                "d": contract["purpose"]["business_purpose"][:400],
                "o": meta["owner"],
                "bp": contract["purpose"]["business_purpose"],
                "rc": meta["risk_class"],
                "ma": contract["autonomy"]["max_level"],
                "st": meta.get("status", "ACTIVE"),
                "v": meta["version"],
            },
        ).one()

        version_row = session.execute(
            text(
                """
                INSERT INTO agent_versions (tenant_id, agent_id, version, contract,
                                            contract_hash, status, published_at)
                VALUES (:t, :a, :v, CAST(:c AS jsonb), :h,
                        CAST('ACTIVE' AS lifecycle_status), now())
                ON CONFLICT (agent_id, version) DO UPDATE
                  SET contract = EXCLUDED.contract, contract_hash = EXCLUDED.contract_hash
                RETURNING id
                """
            ),
            {
                "t": tenant_id,
                "a": agent_row.id,
                "v": meta["version"],
                "c": _j(contract),
                "h": chash,
            },
        ).one()

        session.execute(
            text(
                """
                INSERT INTO agent_contracts (
                  agent_version_id, tenant_id, allowed_models, allowed_tools,
                  allowed_skills, permitted_domains, prohibited_domains,
                  max_classification, token_budget, cost_budget_usd,
                  max_runtime_seconds, max_tool_calls, slo_success_rate,
                  slo_p95_latency_ms, requires_citations, requires_provenance,
                  requires_evaluation, min_evaluation_score)
                VALUES (:av, :t, :models, :tools, :skills, :pd, :xd,
                        CAST(:mc AS data_classification), :tb, :cb, :rt, :tc,
                        :sr, :sl, :rcit, :rprov, :reval, :minscore)
                ON CONFLICT (agent_version_id) DO UPDATE SET
                  allowed_models = EXCLUDED.allowed_models,
                  allowed_tools = EXCLUDED.allowed_tools,
                  allowed_skills = EXCLUDED.allowed_skills,
                  permitted_domains = EXCLUDED.permitted_domains,
                  prohibited_domains = EXCLUDED.prohibited_domains,
                  max_classification = EXCLUDED.max_classification,
                  token_budget = EXCLUDED.token_budget,
                  cost_budget_usd = EXCLUDED.cost_budget_usd,
                  max_runtime_seconds = EXCLUDED.max_runtime_seconds,
                  max_tool_calls = EXCLUDED.max_tool_calls,
                  slo_success_rate = EXCLUDED.slo_success_rate,
                  slo_p95_latency_ms = EXCLUDED.slo_p95_latency_ms,
                  min_evaluation_score = EXCLUDED.min_evaluation_score
                """
            ),
            {
                "av": version_row.id,
                "t": tenant_id,
                "models": contract["models"]["allowed"],
                "tools": contract["tools"].get("allowed", []),
                "skills": contract["skills"]["allowed"],
                "pd": contract["data"]["permitted_domains"],
                "xd": contract["data"]["prohibited_domains"],
                "mc": contract["data"]["max_classification"],
                "tb": limits["token_budget"],
                "cb": limits["cost_budget_usd"],
                "rt": limits["max_runtime_seconds"],
                "tc": limits["max_tool_calls"],
                "sr": contract.get("slo", {}).get("success_rate", 0.95),
                "sl": contract.get("slo", {}).get("p95_latency_ms", 30000),
                "rcit": contract["requirements"]["citations"],
                "rprov": contract["requirements"]["provenance"],
                "reval": evaluation["required"],
                "minscore": evaluation["min_score"],
            },
        )
    return len(registries.agents)


def sync_skills(session: Session, tenant_id: str) -> int:
    registries = load_registries()
    for key, skill in registries.skills.items():
        row = session.execute(
            text(
                """
                INSERT INTO skills (tenant_id, skill_key, name, description, owner_team,
                                    execution_mode, risk_class, current_version)
                VALUES (:t, :k, :n, :d, :o, :em, CAST(:rc AS risk_class), '1.0.0')
                ON CONFLICT (tenant_id, skill_key) DO UPDATE
                  SET name = EXCLUDED.name, description = EXCLUDED.description,
                      owner_team = EXCLUDED.owner_team,
                      execution_mode = EXCLUDED.execution_mode,
                      risk_class = EXCLUDED.risk_class
                RETURNING id
                """
            ),
            {
                "t": tenant_id,
                "k": key,
                "n": skill["name"],
                "d": skill["description"],
                "o": skill.get("owner_team", ""),
                "em": skill["execution_mode"],
                "rc": skill.get("risk_class", "LOW"),
            },
        ).one()
        session.execute(
            text(
                """
                INSERT INTO skill_versions (tenant_id, skill_id, version, input_schema,
                                            output_schema, required_tools,
                                            evaluation_threshold, definition_hash)
                VALUES (:t, :s, '1.0.0', CAST(:i AS jsonb), CAST(:o AS jsonb),
                        :rt, :et, :h)
                ON CONFLICT (skill_id, version) DO UPDATE
                  SET input_schema = EXCLUDED.input_schema,
                      output_schema = EXCLUDED.output_schema,
                      required_tools = EXCLUDED.required_tools,
                      evaluation_threshold = EXCLUDED.evaluation_threshold,
                      definition_hash = EXCLUDED.definition_hash
                """
            ),
            {
                "t": tenant_id,
                "s": row.id,
                "i": _j(skill["input_schema"]),
                "o": _j(skill["output_schema"]),
                "rt": skill.get("required_tools", []),
                "et": skill.get("evaluation_threshold", 0.8),
                "h": content_hash(skill),
            },
        )

    # Grant each agent the skills its contract allows.
    session.execute(text("DELETE FROM agent_skills WHERE tenant_id = :t"), {"t": tenant_id})
    for agent_id, contract in registries.agents.items():
        for skill_key in contract["skills"]["allowed"]:
            session.execute(
                text(
                    """
                    INSERT INTO agent_skills (tenant_id, agent_id, skill_id)
                    SELECT :t, a.id, s.id FROM agents a, skills s
                    WHERE a.tenant_id = :t AND a.agent_key = :ak
                      AND s.tenant_id = :t AND s.skill_key = :sk
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"t": tenant_id, "ak": agent_id, "sk": skill_key},
            )
    return len(registries.skills)


def sync_models(session: Session, tenant_id: str) -> int:
    registries = load_registries()
    for key, model in registries.models.items():
        row = session.execute(
            text(
                """
                INSERT INTO models (tenant_id, model_key, provider, deployment, owner_team,
                                    capabilities, max_classification, context_window,
                                    input_cost_per_1k, output_cost_per_1k, p95_latency_ms,
                                    evaluation_score, known_limitations, residency,
                                    approval_state)
                VALUES (:t, :k, :p, :dep, :o, :caps, CAST(:mc AS data_classification),
                        :cw, :ic, :oc, :lat, :es, :kl, :res, :ap)
                ON CONFLICT (tenant_id, model_key) DO UPDATE SET
                  provider = EXCLUDED.provider, deployment = EXCLUDED.deployment,
                  owner_team = EXCLUDED.owner_team, capabilities = EXCLUDED.capabilities,
                  max_classification = EXCLUDED.max_classification,
                  context_window = EXCLUDED.context_window,
                  input_cost_per_1k = EXCLUDED.input_cost_per_1k,
                  output_cost_per_1k = EXCLUDED.output_cost_per_1k,
                  p95_latency_ms = EXCLUDED.p95_latency_ms,
                  evaluation_score = EXCLUDED.evaluation_score,
                  known_limitations = EXCLUDED.known_limitations,
                  residency = EXCLUDED.residency,
                  approval_state = EXCLUDED.approval_state
                RETURNING id
                """
            ),
            {
                "t": tenant_id,
                "k": key,
                "p": model["provider"],
                "dep": model["deployment"],
                "o": model.get("owner_team", ""),
                "caps": model.get("capabilities", []),
                "mc": model["max_classification"],
                "cw": model["context_window"],
                "ic": model["input_cost_per_1k"],
                "oc": model["output_cost_per_1k"],
                "lat": model["p95_latency_ms"],
                "es": model.get("evaluation_score"),
                "kl": (model.get("known_limitations") or "").strip(),
                "res": model.get("residency", "global"),
                "ap": model.get("approval_state", "PENDING"),
            },
        ).one()
        session.execute(
            text(
                """
                INSERT INTO model_versions (tenant_id, model_id, version, provider_model_id,
                                            evaluation_score)
                VALUES (:t, :m, '1.0.0', :pmid, :es)
                ON CONFLICT (model_id, version) DO UPDATE
                  SET provider_model_id = EXCLUDED.provider_model_id,
                      evaluation_score = EXCLUDED.evaluation_score
                """
            ),
            {
                "t": tenant_id,
                "m": row.id,
                "pmid": model["provider_model_id"],
                "es": model.get("evaluation_score"),
            },
        )
    return len(registries.models)


def sync_tools(session: Session, tenant_id: str) -> int:
    registries = load_registries()
    for key, tool in registries.tools.items():
        session.execute(
            text(
                """
                INSERT INTO tools (tenant_id, tool_key, name, description, owner_team, kind,
                                   connector_key, parameter_schema, scopes,
                                   risk_class, min_autonomy, side_effect, reversibility,
                                   max_classification, rate_limit_per_minute, timeout_seconds,
                                   requires_approval, verification_mode, implementation_status)
                VALUES (:t, :k, :n, :d, :o, :kind, :ck, CAST(:ps AS jsonb), :sc,
                        CAST(:rc AS risk_class), CAST(:ma AS autonomy_level), :se, :rev,
                        CAST(:mc AS data_classification), :rl, :to, :ra, :vm, :is)
                ON CONFLICT (tenant_id, tool_key) DO UPDATE SET
                  name = EXCLUDED.name, description = EXCLUDED.description,
                  owner_team = EXCLUDED.owner_team, kind = EXCLUDED.kind,
                  connector_key = EXCLUDED.connector_key,
                  parameter_schema = EXCLUDED.parameter_schema,
                  scopes = EXCLUDED.scopes, risk_class = EXCLUDED.risk_class,
                  min_autonomy = EXCLUDED.min_autonomy, side_effect = EXCLUDED.side_effect,
                  reversibility = EXCLUDED.reversibility,
                  max_classification = EXCLUDED.max_classification,
                  rate_limit_per_minute = EXCLUDED.rate_limit_per_minute,
                  timeout_seconds = EXCLUDED.timeout_seconds,
                  requires_approval = EXCLUDED.requires_approval,
                  verification_mode = EXCLUDED.verification_mode,
                  implementation_status = EXCLUDED.implementation_status
                """
            ),
            {
                "t": tenant_id,
                "k": key,
                "n": tool["name"],
                "d": tool["description"].strip(),
                "o": tool.get("owner_team", ""),
                "kind": tool["kind"],
                "ck": tool.get("connector_key", ""),
                "ps": _j(tool.get("parameter_schema", {})),
                "sc": tool.get("scopes", []),
                "rc": tool.get("risk_class", "MEDIUM"),
                "ma": tool.get("min_autonomy", "A3"),
                "se": tool["side_effect"],
                "rev": tool.get("reversibility", "REVERSIBLE"),
                "mc": tool.get("max_classification", "INTERNAL"),
                "rl": tool.get("rate_limit_per_minute", 60),
                "to": tool.get("timeout_seconds", 30),
                "ra": tool.get("requires_approval", False),
                "vm": tool.get("verification_mode", "NONE"),
                "is": tool["implementation_status"],
            },
        )
    return len(registries.tools)


def sync_policies(session: Session, tenant_id: str) -> int:
    registries = load_registries()
    for key, policy in registries.policies.items():
        row = session.execute(
            text(
                """
                INSERT INTO policies (tenant_id, policy_key, name, description, category,
                                      owner_team, enforcement)
                VALUES (:t, :k, :n, :d, :c, :o, :e)
                ON CONFLICT (tenant_id, policy_key) DO UPDATE SET
                  name = EXCLUDED.name, description = EXCLUDED.description,
                  category = EXCLUDED.category, owner_team = EXCLUDED.owner_team,
                  enforcement = EXCLUDED.enforcement
                RETURNING id, current_version
                """
            ),
            {
                "t": tenant_id,
                "k": key,
                "n": policy["name"],
                "d": (policy.get("description") or "").strip(),
                "c": policy.get("category", "general"),
                "o": policy.get("owner_team", ""),
                "e": policy.get("enforcement", "ENFORCE"),
            },
        ).one()
        session.execute(
            text(
                """
                INSERT INTO policy_versions (tenant_id, policy_id, version, rules, rules_hash)
                VALUES (:t, :p, :v, CAST(:r AS jsonb), :h)
                ON CONFLICT (policy_id, version) DO UPDATE
                  SET rules = EXCLUDED.rules, rules_hash = EXCLUDED.rules_hash
                """
            ),
            {
                "t": tenant_id,
                "p": row.id,
                "v": row.current_version,
                "r": _j(policy["rules"]),
                "h": content_hash(policy["rules"]),
            },
        )
    return len(registries.policies)


def sync_prompts(session: Session, tenant_id: str) -> int:
    import yaml

    registry_file = PROMPTS_DIR / "registry.yaml"
    if not registry_file.exists():
        return 0
    doc = yaml.safe_load(registry_file.read_text(encoding="utf-8"))
    count = 0
    for spec in doc.get("prompts", []):
        body = (PROMPTS_DIR / spec["body_file"]).read_text(encoding="utf-8")
        row = session.execute(
            text(
                """
                INSERT INTO prompts (tenant_id, prompt_key, purpose, owning_agent_key,
                                     current_version)
                VALUES (:t, :k, :p, :a, :v)
                ON CONFLICT (tenant_id, prompt_key) DO UPDATE SET
                  purpose = EXCLUDED.purpose, owning_agent_key = EXCLUDED.owning_agent_key,
                  current_version = EXCLUDED.current_version
                RETURNING id
                """
            ),
            {
                "t": tenant_id,
                "k": spec["key"],
                "p": spec["purpose"],
                "a": spec.get("owning_agent", ""),
                "v": spec["version"],
            },
        ).one()
        session.execute(
            text(
                """
                INSERT INTO prompt_versions (tenant_id, prompt_id, version, body, body_hash,
                                             deployment_status, effective_from)
                VALUES (:t, :p, :v, :b, :h, :ds, now())
                ON CONFLICT (prompt_id, version) DO UPDATE SET
                  body = EXCLUDED.body, body_hash = EXCLUDED.body_hash,
                  deployment_status = EXCLUDED.deployment_status
                """
            ),
            {
                "t": tenant_id,
                "p": row.id,
                "v": spec["version"],
                "b": body,
                "h": content_hash(body),
                "ds": spec.get("deployment_status", "DRAFT"),
            },
        )
        count += 1
    return count


def sync_controls(session: Session, tenant_id: str) -> int:
    """Load the assurance control catalogue if it is present."""
    import yaml

    from agentic_os.core.registry import CONTROLS_FILE

    if not CONTROLS_FILE.exists():
        return 0
    doc = yaml.safe_load(CONTROLS_FILE.read_text(encoding="utf-8"))
    count = 0
    for control in doc.get("controls", []):
        session.execute(
            text(
                """
                INSERT INTO controls (tenant_id, control_id, domain, title, requirement,
                                      implementation, weight, critical, applicable,
                                      standard_mappings, automated_test, expected_result,
                                      owner_team, evidence_ttl_days)
                VALUES (:t, :cid, :dom, :ti, :req, :impl, :w, :crit, :app,
                        CAST(:sm AS jsonb), :at, :er, :ot, :ttl)
                ON CONFLICT (tenant_id, control_id) DO UPDATE SET
                  domain = EXCLUDED.domain, title = EXCLUDED.title,
                  requirement = EXCLUDED.requirement,
                  implementation = EXCLUDED.implementation, weight = EXCLUDED.weight,
                  critical = EXCLUDED.critical, applicable = EXCLUDED.applicable,
                  standard_mappings = EXCLUDED.standard_mappings,
                  automated_test = EXCLUDED.automated_test,
                  expected_result = EXCLUDED.expected_result,
                  owner_team = EXCLUDED.owner_team,
                  evidence_ttl_days = EXCLUDED.evidence_ttl_days
                """
            ),
            {
                "t": tenant_id,
                "cid": control["id"],
                "dom": control["domain"],
                "ti": control["title"],
                "req": control["requirement"],
                "impl": control.get("implementation", ""),
                "w": control.get("weight", 1),
                "crit": control.get("critical", False),
                "app": control.get("applicable", True),
                "sm": _j(control.get("standards", [])),
                "at": control.get("test", ""),
                "er": control.get("expected", ""),
                "ot": control.get("owner_team", ""),
                "ttl": control.get("evidence_ttl_days", 90),
            },
        )
        count += 1
    return count


def seed_budgets_and_switches(session: Session, tenant_id: str) -> int:
    """Create default budgets and the kill-switch rows the platform reads."""
    session.execute(
        text(
            """
            INSERT INTO budgets (tenant_id, scope, scope_key, period, cost_cap_usd,
                                 token_cap, hard_stop, fallback_model_key)
            VALUES (:t, 'TENANT', '', 'DAY', 250, 20000000, true, 'deterministic-local'),
                   (:t, 'RUN', '', 'RUN', 5, 250000, true, 'deterministic-local')
            ON CONFLICT (tenant_id, scope, scope_key, period) DO NOTHING
            """
        ),
        {"t": tenant_id},
    )
    for scope in ("TENANT", "READ_ONLY"):
        session.execute(
            text(
                """
                INSERT INTO kill_switches (tenant_id, scope, target_key, engaged)
                VALUES (:t, :s, '', false)
                ON CONFLICT (tenant_id, scope, target_key)
                  WHERE tenant_id IS NOT NULL DO NOTHING
                """
            ),
            {"t": tenant_id, "s": scope},
        )
    return 2


#: Record of processing activities. These describe what this platform actually
#: does with personal data — they are not sample rows. Each one is traceable to
#: a table and a code path in this repository.
PROCESSING_ACTIVITIES: tuple[dict[str, Any], ...] = (
    {
        "activity": "workforce-identity",
        "purpose": "Authenticate staff and authorise their access to the platform",
        "legal_basis": "Contract (employment) and legitimate interest in securing systems",
        "data_categories": ["name", "work email", "authentication factors", "IP address"],
        "subject_categories": ["employees", "contractors"],
        "recipients": ["internal platform operations"],
        "cross_border": False,
        "retention": "Duration of engagement plus 12 months; sessions purged 30 days after expiry",
        "controller": "RTA",
    },
    {
        "activity": "agent-run-audit",
        "purpose": "Record who requested each agent run and what it did, for accountability",
        "legal_basis": "Legal obligation and legitimate interest in auditability",
        "data_categories": ["user identifier", "request text", "decisions taken"],
        "subject_categories": ["employees"],
        "recipients": ["internal audit", "regulators on lawful request"],
        "cross_border": False,
        "retention": "Append-only ledger retained for the statutory audit period",
        "controller": "RTA",
    },
    {
        "activity": "knowledge-ingestion",
        "purpose": "Index operational documents so agents can answer from cited sources",
        "legal_basis": "Legitimate interest in operational efficiency",
        "data_categories": ["personal data incidentally present in ingested documents"],
        "subject_categories": ["employees", "contractors", "members of the public"],
        "recipients": ["internal users cleared for the document classification"],
        "cross_border": False,
        "retention": "Per document retention_until; detected identifiers redacted at ingestion",
        "controller": "RTA",
    },
    {
        "activity": "model-invocation",
        "purpose": "Generate and analyse text in support of a requested task",
        "legal_basis": "Legitimate interest in operational efficiency",
        "data_categories": ["prompt content", "retrieved excerpts"],
        "subject_categories": ["employees"],
        "recipients": ["configured model providers subject to the model registry"],
        "cross_border": False,
        "retention": "Prompts and completions are not retained beyond the run record",
        "controller": "RTA",
    },
)


def seed_processing_records(session: Session, tenant_id: str) -> int:
    """Register the record of processing activities."""
    for activity in PROCESSING_ACTIVITIES:
        session.execute(
            text(
                """
                INSERT INTO processing_records (tenant_id, activity, purpose, legal_basis,
                                                data_categories, subject_categories, recipients,
                                                cross_border, retention, controller)
                VALUES (:t, :activity, :purpose, :legal_basis, :data_categories,
                        :subject_categories, :recipients, :cross_border, :retention, :controller)
                ON CONFLICT (tenant_id, activity) DO UPDATE SET
                    purpose = EXCLUDED.purpose,
                    legal_basis = EXCLUDED.legal_basis,
                    data_categories = EXCLUDED.data_categories,
                    subject_categories = EXCLUDED.subject_categories,
                    recipients = EXCLUDED.recipients,
                    cross_border = EXCLUDED.cross_border,
                    retention = EXCLUDED.retention,
                    controller = EXCLUDED.controller
                """
            ),
            {"t": tenant_id, **activity},
        )
    return len(PROCESSING_ACTIVITIES)


def seed_domain(tenant_id: str, organization_id: str) -> dict[str, int]:
    """Synchronise registries and seed the demo corpus for one tenant."""
    summary: dict[str, int] = {}
    with provisioning_session_scope() as session:
        bind_tenant(session, tenant_id)
        summary["agents"] = sync_agents(session, tenant_id)
        summary["skills"] = sync_skills(session, tenant_id)
        summary["models"] = sync_models(session, tenant_id)
        summary["tools"] = sync_tools(session, tenant_id)
        summary["policies"] = sync_policies(session, tenant_id)
        summary["prompts"] = sync_prompts(session, tenant_id)
        summary["controls"] = sync_controls(session, tenant_id)
        summary["budgets"] = seed_budgets_and_switches(session, tenant_id)
        summary["processing_records"] = seed_processing_records(session, tenant_id)

    from agentic_os.core.seed_corpus import seed_corpus

    summary.update(seed_corpus(tenant_id, organization_id))
    return summary
