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
    # Decision intelligence.
    #
    # ``decisions:read`` grants the *right* to read decisions; it does not grant
    # access to any particular decision. Domain membership does that, and is
    # evaluated inside the SQL predicate, so a holder of this permission with no
    # membership sees zero rows rather than a filtered list.
    _p("decisions:read", "View decision cases"),
    _p("decisions:create", "Raise a decision case", "MEDIUM"),
    _p("decisions:analyse", "Add options, evidence and recommendations", "MEDIUM"),
    _p("decisions:review", "Review a recommendation and send it for approval", "HIGH"),
    _p("decisions:approve", "Approve or reject a decision", "CRITICAL"),
    _p("decisions:execute", "Dispatch the approved action", "CRITICAL"),
    _p("decisions:verify", "Record a verified outcome against its target", "HIGH"),
    _p("kpis:read", "View KPI definitions and values"),
    _p("kpis:write", "Define KPIs and their targets", "HIGH"),
    _p("notifications:read", "Read your own notification inbox"),
)

CATALOGUE_BY_ID = {p.id: p for p in CATALOGUE}


def _ids(*prefixes: str) -> tuple[str, ...]:
    return tuple(p.id for p in CATALOGUE if p.id.split(":", 1)[0] in prefixes)


#: Authority over a business decision, as distinct from authority over the
#: platform that supports it. The Platform Administrator role is granted every
#: permission in the catalogue, so without this exclusion the person who
#: administers the AI platform would silently acquire the power to review,
#: approve, execute and verify the organisation's decisions — a separation of
#: duties failure that no amount of audit logging repairs after the fact. These
#: four are held only by roles that carry the corresponding accountability.
BUSINESS_DECISION_AUTHORITY = frozenset(
    {"decisions:review", "decisions:approve", "decisions:execute", "decisions:verify"}
)

#: Reads that must not be granted by a blanket "read everything" role. The
#: audit ledger and the privacy register expose who did what and whose personal
#: data is held; both are assurance surfaces, not operational ones.
SENSITIVE_READS = frozenset({"audit:read", "privacy:read"})

#: The baseline read grant shared by operational roles.
READ_ONLY = tuple(p.id for p in CATALOGUE if p.action == "read" and p.id not in SENSITIVE_READS)


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
        permissions=READ_ONLY
        + ("approvals:decide", "runs:create", "decisions:review", "decisions:approve"),
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
        slug="engineer",
        name="Engineer",
        description=(
            "Raises decision cases from the field and supplies the evidence "
            "behind them. Cannot review, approve or execute their own case."
        ),
        permissions=READ_ONLY
        + (
            "runs:create",
            "tasks:write",
            "incidents:write",
            "decisions:create",
            "decisions:analyse",
        ),
        max_autonomy="A1",
    ),
    SystemRole(
        slug="section_lead",
        name="Section Lead",
        description=(
            "Reviews recommendations within their section and sends them for "
            "approval. Reviewing is deliberately not approving: the brief "
            "requires REVIEW and APPROVE to be separate stations, and a role "
            "holding both collapses them."
        ),
        permissions=READ_ONLY
        + (
            "runs:create",
            "tasks:write",
            "incidents:write",
            "workflows:execute",
            "decisions:create",
            "decisions:analyse",
            "decisions:review",
            "decisions:verify",
        ),
        max_autonomy="A2",
    ),
    SystemRole(
        slug="department_manager",
        name="Department Manager",
        description=(
            "Accountable for decisions across a department: approves within "
            "authority, owns KPI targets and signs off verified outcomes."
        ),
        permissions=READ_ONLY
        + (
            "runs:create",
            "approvals:decide",
            "decisions:create",
            "decisions:review",
            "decisions:approve",
            "decisions:execute",
            "decisions:verify",
            "kpis:write",
            "outcomes:write",
        ),
        requires_mfa=True,
        max_autonomy="A2",
    ),
    SystemRole(
        slug="approver",
        name="Approver",
        description="Holds human authorization authority for A4 actions.",
        permissions=READ_ONLY + ("approvals:decide", "approvals:delegate", "decisions:approve"),
        requires_mfa=True,
        max_autonomy="A2",
    ),
    SystemRole(
        slug="auditor",
        name="Auditor",
        description="Read-only assurance access including the audit ledger.",
        permissions=READ_ONLY + ("audit:read", "audit:verify", "privacy:read"),
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
            "audit:read",
            "audit:verify",
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
            "audit:read",
            "audit:verify",
            "privacy:read",
        ),
        requires_mfa=True,
        max_autonomy="A2",
    ),
    SystemRole(
        slug="platform_admin",
        name="Platform Administrator",
        description=(
            "Full administrative authority over the platform within the tenant. "
            "Explicitly excludes authority over business decisions."
        ),
        permissions=tuple(
            p.id for p in CATALOGUE if p.id not in BUSINESS_DECISION_AUTHORITY
        ),
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
