"""Permission catalogue and system role definitions.

Permissions are ``resource:action`` pairs. The catalogue is the single source
of truth: migration seeds and the authorization engine both read it, so a role
cannot be granted a permission that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Permission:
    id: str
    description: str
    risk: str = "LOW"

    @property
    def resource(self) -> str:
        return self.id.split(":", 1)[0]

    @property
    def action(self) -> str:
        return self.id.split(":", 1)[1]


def _p(pid: str, description: str, risk: str = "LOW") -> Permission:
    return Permission(pid, description, risk)


CATALOGUE: tuple[Permission, ...] = (
    # Runs and tasks
    _p("runs:read", "View runs and run detail"),
    _p("runs:create", "Submit an objective and start a run", "MEDIUM"),
    _p("runs:cancel", "Cancel a running execution", "MEDIUM"),
    _p("tasks:read", "View tasks"),
    _p("tasks:write", "Create and update tasks"),
    # Approvals
    _p("approvals:read", "View approval requests"),
    _p("approvals:decide", "Approve or reject a request", "HIGH"),
    _p("approvals:delegate", "Delegate approval authority", "HIGH"),
    # Agents, skills, workflows
    _p("agents:read", "View agents and contracts"),
    _p("agents:write", "Create or modify agents", "HIGH"),
    _p("agents:publish", "Publish an agent contract version", "HIGH"),
    _p("skills:read", "View skills"),
    _p("skills:write", "Create or modify skills", "MEDIUM"),
    _p("workflows:read", "View workflows and workflow runs"),
    _p("workflows:write", "Create or modify workflow definitions", "HIGH"),
    _p("workflows:execute", "Start a workflow run", "MEDIUM"),
    # AI plane
    _p("models:read", "View the model registry"),
    _p("models:write", "Register or approve models", "HIGH"),
    _p("prompts:read", "View the prompt registry"),
    _p("prompts:write", "Create prompt versions", "MEDIUM"),
    _p("prompts:deploy", "Deploy a prompt version to production", "HIGH"),
    # Tools and connectors
    _p("tools:read", "View the tool registry"),
    _p("tools:write", "Register or modify tools", "HIGH"),
    _p("tools:invoke", "Invoke tools through the gateway", "MEDIUM"),
    _p("connectors:read", "View connectors"),
    _p("connectors:write", "Configure connectors and credentials", "CRITICAL"),
    _p("mcp:read", "View the MCP registry"),
    _p("mcp:write", "Register or classify MCP servers", "CRITICAL"),
    # Knowledge
    _p("knowledge:read", "Search governed knowledge"),
    _p("knowledge:write", "Upload and publish documents", "MEDIUM"),
    _p("knowledge:admin", "Manage document ACLs and classification", "HIGH"),
    _p("graph:read", "Query the enterprise intelligence graph"),
    # Governance and assurance
    _p("policies:read", "View policies"),
    _p("policies:write", "Author and publish policies", "CRITICAL"),
    _p("risks:read", "View risk assessments"),
    _p("evidence:read", "View evidence and maturity reports"),
    _p("evidence:write", "Record control evidence", "HIGH"),
    _p("evaluations:read", "View evaluation results"),
    _p("evaluations:run", "Execute evaluation suites", "MEDIUM"),
    _p("audit:read", "Read the audit ledger", "MEDIUM"),
    _p("audit:verify", "Verify audit chain integrity", "MEDIUM"),
    # Security and operations
    _p("security:read", "View security findings and posture"),
    _p("security:admin", "Manage security configuration", "CRITICAL"),
    _p("killswitch:read", "View kill switch state"),
    _p("killswitch:engage", "Engage or release a kill switch", "CRITICAL"),
    _p("incidents:read", "View incidents and alerts"),
    _p("incidents:write", "Create and update incidents", "MEDIUM"),
    # Organisation and administration
    _p("org:read", "View organization and tenant settings"),
    _p("org:write", "Modify organization and tenant settings", "HIGH"),
    _p("users:read", "View users and role assignments"),
    _p("users:write", "Create users and assign roles", "CRITICAL"),
    _p("privacy:read", "View privacy records and DSARs"),
    _p("privacy:write", "Process data subject requests", "HIGH"),
    # Analytics and outcomes
    _p("analytics:read", "View analytics and reports"),
    _p("outcomes:read", "View business outcome records"),
    _p("outcomes:write", "Record business outcomes", "MEDIUM"),
    _p("costs:read", "View cost and budget data"),
    _p("costs:write", "Manage budgets", "HIGH"),
)

CATALOGUE_BY_ID = {p.id: p for p in CATALOGUE}


def _ids(*prefixes: str) -> tuple[str, ...]:
    return tuple(p.id for p in CATALOGUE if p.id.split(":", 1)[0] in prefixes)


READ_ONLY = tuple(p.id for p in CATALOGUE if p.action in ("read",))


@dataclass(frozen=True, slots=True)
class SystemRole:
    slug: str
    name: str
    description: str
    permissions: tuple[str, ...]
    requires_mfa: bool = False
    max_autonomy: str = "A2"


SYSTEM_ROLES: tuple[SystemRole, ...] = (
    SystemRole(
        slug="executive",
        name="Executive",
        description="Business oversight: outcomes, risk posture and approvals.",
        permissions=READ_ONLY + ("approvals:decide", "runs:create"),
        max_autonomy="A2",
    ),
    SystemRole(
        slug="operator",
        name="Operator",
        description="Day-to-day operation of runs, workflows and incidents.",
        permissions=READ_ONLY
        + (
            "runs:create",
            "runs:cancel",
            "tasks:write",
            "workflows:execute",
            "tools:invoke",
            "incidents:write",
            "knowledge:write",
        ),
        max_autonomy="A3",
    ),
    SystemRole(
        slug="analyst",
        name="Analyst",
        description="Analysis over governed knowledge and analytics.",
        permissions=READ_ONLY + ("runs:create", "evaluations:run"),
        max_autonomy="A1",
    ),
    SystemRole(
        slug="builder",
        name="Builder",
        description="Builds agents, skills, workflows and prompts.",
        permissions=READ_ONLY
        + (
            "runs:create",
            "agents:write",
            "skills:write",
            "workflows:write",
            "workflows:execute",
            "prompts:write",
            "tools:invoke",
        ),
        max_autonomy="A2",
    ),
    SystemRole(
        slug="approver",
        name="Approver",
        description="Holds human authorization authority for A4 actions.",
        permissions=READ_ONLY + ("approvals:decide", "approvals:delegate"),
        requires_mfa=True,
        max_autonomy="A2",
    ),
    SystemRole(
        slug="auditor",
        name="Auditor",
        description="Read-only assurance access including the audit ledger.",
        permissions=READ_ONLY + ("audit:verify",),
        requires_mfa=True,
        max_autonomy="A0",
    ),
    SystemRole(
        slug="security_admin",
        name="Security Administrator",
        description="Security configuration, kill switches and MCP trust.",
        permissions=READ_ONLY
        + (
            "security:admin",
            "killswitch:engage",
            "mcp:write",
            "connectors:write",
            "policies:write",
            "incidents:write",
        ),
        requires_mfa=True,
        max_autonomy="A2",
    ),
    SystemRole(
        slug="governance_admin",
        name="Governance Administrator",
        description="Policy, evidence, evaluation and privacy administration.",
        permissions=READ_ONLY
        + (
            "policies:write",
            "evidence:write",
            "evaluations:run",
            "privacy:write",
            "outcomes:write",
            "models:write",
            "prompts:deploy",
            "agents:publish",
        ),
        requires_mfa=True,
        max_autonomy="A2",
    ),
    SystemRole(
        slug="platform_admin",
        name="Platform Administrator",
        description="Full administrative authority within the tenant.",
        permissions=tuple(p.id for p in CATALOGUE),
        requires_mfa=True,
        max_autonomy="A3",
    ),
)

SYSTEM_ROLES_BY_SLUG = {r.slug: r for r in SYSTEM_ROLES}


def validate_catalogue() -> list[str]:
    """Return problems such as roles referencing unknown permissions."""
    problems: list[str] = []
    for role in SYSTEM_ROLES:
        for pid in role.permissions:
            if pid not in CATALOGUE_BY_ID:
                problems.append(f"role {role.slug} references unknown permission {pid}")
    seen: set[str] = set()
    for perm in CATALOGUE:
        if perm.id in seen:
            problems.append(f"duplicate permission {perm.id}")
        seen.add(perm.id)
    return problems
