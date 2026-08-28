"""Execution context: the four identities that every governed action carries.

Architecture Constitution rule 14 requires that every external action traces to
a human, an agent, a workflow and a tool identity. :class:`ExecutionContext` is
the in-process carrier for those identities plus the tenancy and correlation
data that the database (via RLS), the audit ledger and the observability layer
all key off.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from agentic_os.core.ids import correlation_id as new_correlation_id

DataClassification = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]

#: Ordered from least to most sensitive; used for dominance comparisons.
CLASSIFICATION_ORDER: tuple[DataClassification, ...] = (
    "PUBLIC",
    "INTERNAL",
    "CONFIDENTIAL",
    "RESTRICTED",
)


#: Maps an untrusted string to a real classification. A dict rather than a
#: membership test because the latter cannot narrow `str` to a Literal.
_KNOWN_CLASSIFICATIONS: dict[str, DataClassification] = {
    "PUBLIC": "PUBLIC",
    "INTERNAL": "INTERNAL",
    "CONFIDENTIAL": "CONFIDENTIAL",
    "RESTRICTED": "RESTRICTED",
}


def as_classification(value: object) -> DataClassification:
    """Narrow an untrusted value to a classification, clamping to PUBLIC.

    Classifications reach the platform from three places that are not the
    application's own code: a database column, a JWT claim, and a tool
    parameter. The first is a PostgreSQL enum and cannot be wrong; the other two
    are only as good as the signature or the schema in front of them.

    Clamping to PUBLIC — the *least* privileged value — is the safe direction
    for anything used as a clearance or a ceiling. Note the asymmetry with
    `classification_rank`, which ranks an unknown value as maximally sensitive:
    that is correct for a document's own label and exactly wrong for a viewer's
    clearance, where it would admit everything.
    """
    return _KNOWN_CLASSIFICATIONS.get(str(value), "PUBLIC")


def classification_rank(value: str) -> int:
    try:
        return CLASSIFICATION_ORDER.index(value)
    except ValueError:
        # Unknown classifications are treated as the most sensitive.
        return len(CLASSIFICATION_ORDER)


@dataclass(frozen=True, slots=True)
class HumanIdentity:
    user_id: str
    email: str
    display_name: str = ""
    roles: frozenset[str] = field(default_factory=frozenset)
    permissions: frozenset[str] = field(default_factory=frozenset)
    groups: frozenset[str] = field(default_factory=frozenset)
    mfa_satisfied: bool = False
    session_id: str = ""
    clearance: DataClassification = "INTERNAL"


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    agent_id: str
    agent_version: str
    autonomy_level: str
    risk_class: str = "MEDIUM"


@dataclass(frozen=True, slots=True)
class WorkflowIdentity:
    workflow_id: str
    workflow_run_id: str
    workflow_version: str = ""


@dataclass(frozen=True, slots=True)
class ToolIdentity:
    tool_id: str
    connector_id: str = ""
    scopes: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Immutable carrier of identity, tenancy and correlation for one request."""

    tenant_id: str
    organization_id: str
    human: HumanIdentity | None = None
    agent: AgentIdentity | None = None
    workflow: WorkflowIdentity | None = None
    tool: ToolIdentity | None = None
    correlation_id: str = field(default_factory=new_correlation_id)
    trace_id: str = ""
    run_id: str = ""
    parent_run_id: str = ""
    environment: str = "development"
    service_principal: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    # -- derivation ------------------------------------------------------
    def with_agent(self, agent: AgentIdentity) -> ExecutionContext:
        return replace(self, agent=agent)

    def with_workflow(self, workflow: WorkflowIdentity) -> ExecutionContext:
        return replace(self, workflow=workflow)

    def with_tool(self, tool: ToolIdentity) -> ExecutionContext:
        return replace(self, tool=tool)

    def with_run(self, run_id: str, parent_run_id: str = "") -> ExecutionContext:
        return replace(self, run_id=run_id, parent_run_id=parent_run_id or self.parent_run_id)

    # -- projection ------------------------------------------------------
    @property
    def actor_id(self) -> str:
        """The identity primarily responsible for the current action."""
        if self.agent is not None:
            return self.agent.agent_id
        if self.human is not None:
            return self.human.user_id
        return self.service_principal or "system"

    @property
    def actor_type(self) -> str:
        if self.agent is not None:
            return "AGENT"
        if self.human is not None:
            return "HUMAN"
        if self.service_principal:
            return "SERVICE"
        return "SYSTEM"

    def audit_identities(self) -> dict[str, Any]:
        """Flatten the four identities for the audit ledger."""
        return {
            "human_id": self.human.user_id if self.human else None,
            "agent_id": self.agent.agent_id if self.agent else None,
            "agent_version": self.agent.agent_version if self.agent else None,
            "workflow_run_id": self.workflow.workflow_run_id if self.workflow else None,
            "tool_id": self.tool.tool_id if self.tool else None,
            "service_principal": self.service_principal or None,
        }

    def has_permission(self, permission: str) -> bool:
        if self.human is None:
            return False
        if permission in self.human.permissions:
            return True
        # Wildcard grants such as "runs:*" or "*".
        if "*" in self.human.permissions:
            return True
        resource = permission.split(":", 1)[0]
        return f"{resource}:*" in self.human.permissions


_current: contextvars.ContextVar[ExecutionContext | None] = contextvars.ContextVar(
    "agentic_execution_context", default=None
)


def current_context() -> ExecutionContext | None:
    return _current.get()


def set_current_context(ctx: ExecutionContext | None) -> contextvars.Token:
    return _current.set(ctx)


def reset_current_context(token: contextvars.Token) -> None:
    _current.reset(token)


class use_context:  # noqa: N801 - context-manager naming
    """Bind an :class:`ExecutionContext` for the duration of a block."""

    def __init__(self, ctx: ExecutionContext) -> None:
        self._ctx = ctx
        self._token: contextvars.Token | None = None

    def __enter__(self) -> ExecutionContext:
        self._token = _current.set(self._ctx)
        return self._ctx

    def __exit__(self, *exc: object) -> Literal[False]:
        if self._token is not None:
            _current.reset(self._token)
        return False


def system_context(tenant_id: str, organization_id: str, principal: str) -> ExecutionContext:
    """Context for platform-internal work (workers, schedulers, migrations)."""
    return ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        service_principal=principal,
    )
