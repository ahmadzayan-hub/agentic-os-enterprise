"""Agent runtime.

An agent is a contract plus an execution budget, not a prompt. The runtime:

* resolves the agent's published contract version and pins it for the whole run,
  so a mid-run contract change cannot alter what is already executing;
* enforces every declared limit — tokens, cost, wall-clock, tool calls — and
  stops the moment one is reached rather than after;
* screens everything entering the model context through the context firewall;
* requires citations and provenance where the contract demands them, and fails
  the step if they are absent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.ai.context_firewall import ScreenedContext, TrustTier, screen
from agentic_os.ai.model_gateway import ModelGateway
from agentic_os.assurance.audit import AuditEntry, AuditLedger
from agentic_os.core.context import AgentIdentity, ExecutionContext
from agentic_os.core.errors import (
    BudgetExceeded,
    ContractViolation,
    NotFound,
    ValidationError,
)
from agentic_os.core.ids import prefixed_id
from agentic_os.core.registry import load_registries
from agentic_os.runtime.skills import SkillExecutor, SkillResult
from agentic_os.tools.gateway import ToolGateway


@dataclass(slots=True)
class AgentBudget:
    """Live consumption against the contract's declared limits."""

    token_budget: int
    cost_budget_usd: float
    max_runtime_seconds: int
    max_tool_calls: int
    tokens_used: int = 0
    cost_used_usd: float = 0.0
    tool_calls_used: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    def check(self) -> None:
        if self.tokens_used >= self.token_budget:
            raise BudgetExceeded(
                "agent token budget exhausted",
                details={"limit": self.token_budget, "used": self.tokens_used},
            )
        if self.cost_used_usd >= self.cost_budget_usd:
            raise BudgetExceeded(
                "agent cost budget exhausted",
                details={"limit_usd": self.cost_budget_usd, "used_usd": self.cost_used_usd},
            )
        if self.elapsed_seconds >= self.max_runtime_seconds:
            raise BudgetExceeded(
                "agent runtime limit reached",
                details={
                    "limit_seconds": self.max_runtime_seconds,
                    "elapsed_seconds": round(self.elapsed_seconds, 2),
                },
            )
        if self.tool_calls_used >= self.max_tool_calls and self.max_tool_calls >= 0:
            raise BudgetExceeded(
                "agent tool-call limit reached",
                details={"limit": self.max_tool_calls, "used": self.tool_calls_used},
            )

    def remaining(self) -> dict[str, Any]:
        return {
            "tokens": max(0, self.token_budget - self.tokens_used),
            "cost_usd": round(max(0.0, self.cost_budget_usd - self.cost_used_usd), 6),
            "seconds": round(max(0.0, self.max_runtime_seconds - self.elapsed_seconds), 2),
            "tool_calls": max(0, self.max_tool_calls - self.tool_calls_used),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokens_used": self.tokens_used,
            "cost_used_usd": round(self.cost_used_usd, 6),
            "tool_calls_used": self.tool_calls_used,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "remaining": self.remaining(),
        }


@dataclass(slots=True)
class AgentSession:
    """One agent's bounded execution within a run."""

    agent_key: str
    agent_version: str
    contract: dict[str, Any]
    budget: AgentBudget
    context: ScreenedContext = field(default_factory=ScreenedContext)
    citations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def identity(self) -> AgentIdentity:
        return AgentIdentity(
            agent_id=self.agent_key,
            agent_version=self.agent_version,
            autonomy_level=self.contract["autonomy"]["max_level"],
            risk_class=self.contract["agent"]["risk_class"],
        )


class AgentRuntime:
    """Instantiates agents against their published contracts and runs skills."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._models = ModelGateway(session)
        self._tools = ToolGateway(session)
        self._skills = SkillExecutor(session, self._tools, self._models)
        self._ledger = AuditLedger(session)

    # -- lifecycle ---------------------------------------------------------
    def open(self, ctx: ExecutionContext, agent_key: str) -> AgentSession:
        """Resolve and pin the agent's published contract version."""
        row = (
            self._session.execute(
                text(
                    """
                SELECT a.agent_key, av.version, av.contract, av.status
                FROM agents a
                JOIN agent_versions av ON av.agent_id = a.id AND av.tenant_id = a.tenant_id
                WHERE a.tenant_id = :t AND a.agent_key = :k
                  AND av.version = a.current_version AND a.status = 'ACTIVE'
                """
                ),
                {"t": ctx.tenant_id, "k": agent_key},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound(f"agent '{agent_key}' has no active published contract in this tenant")
        if row["status"] != "ACTIVE":
            raise ContractViolation(f"agent '{agent_key}' contract version is {row['status']}")

        contract = row["contract"]
        limits = contract["limits"]
        return AgentSession(
            agent_key=agent_key,
            agent_version=row["version"],
            contract=contract,
            budget=AgentBudget(
                token_budget=int(limits["token_budget"]),
                cost_budget_usd=float(limits["cost_budget_usd"]),
                max_runtime_seconds=int(limits["max_runtime_seconds"]),
                max_tool_calls=int(limits["max_tool_calls"]),
            ),
        )

    def bind(self, ctx: ExecutionContext, agent: AgentSession) -> ExecutionContext:
        """Return a context carrying the agent identity and its contract ceiling."""
        return ctx.with_agent(agent.identity)

    # -- context assembly --------------------------------------------------
    def add_context(
        self,
        agent: AgentSession,
        content: str,
        tier: TrustTier,
        *,
        source_ref: str = "",
    ) -> None:
        """Screen and attach content to the agent's model context."""
        agent.context.add(screen(content, tier, source_ref=source_ref))

    # -- execution ---------------------------------------------------------
    def run_skill(
        self,
        ctx: ExecutionContext,
        agent: AgentSession,
        skill_key: str,
        params: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> SkillResult:
        """Execute one skill under the agent's contract and budget."""
        agent.budget.check()

        allowed = set(agent.contract["skills"]["allowed"])
        if skill_key not in allowed:
            raise ContractViolation(
                f"agent '{agent.agent_key}' may not use skill '{skill_key}'",
                details={"allowed": sorted(allowed)},
            )

        provenance = agent.context.provenance()
        # ExecutionContext is a frozen slots dataclass, so derive rather than mutate.
        agent_ctx = replace(
            self.bind(ctx, agent),
            attributes={
                **ctx.attributes,
                "origin_trust_tier": provenance["lowest_trust_tier"],
                "injection_detected": provenance["injection_detected"],
                "classification": agent.contract["data"]["max_classification"],
            },
        )

        registries = load_registries()
        skill = registries.skills.get(skill_key)
        if skill is None:
            raise ValidationError(f"skill '{skill_key}' is not registered")

        before_tool_calls = self._tool_call_count(ctx)
        result = self._skills.execute(
            agent_ctx,
            skill_key,
            params,
            idempotency_key=idempotency_key or prefixed_id("skill"),
        )
        after_tool_calls = self._tool_call_count(ctx)

        agent.budget.tokens_used += result.input_tokens + result.output_tokens
        agent.budget.cost_used_usd += result.cost_usd
        agent.budget.tool_calls_used += max(0, after_tool_calls - before_tool_calls)

        requirements = agent.contract["requirements"]
        if requirements.get("citations") and skill_key in ("summarise", "analyse"):
            if not result.citations and not result.output.get("citations"):
                raise ContractViolation(
                    f"agent '{agent.agent_key}' requires citations but skill '{skill_key}' produced none",
                    details={"skill": skill_key},
                )
        for citation in result.citations:
            agent.citations.append({"source_id": citation, "skill": skill_key})

        self._ledger.append(
            agent_ctx,
            AuditEntry(
                category="AGENT_ACTION",
                action=f"agent.skill.{skill_key}",
                resource_type="skill",
                resource_id=skill_key,
                payload={
                    "deterministic": result.deterministic,
                    "model_key": result.model_key,
                    "cost_usd": result.cost_usd,
                    "tokens": result.input_tokens + result.output_tokens,
                    "budget": agent.budget.to_dict(),
                    "citations": result.citations,
                },
            ),
        )
        return result

    def invoke_tool(
        self,
        ctx: ExecutionContext,
        agent: AgentSession,
        tool_key: str,
        parameters: dict[str, Any],
        *,
        idempotency_key: str,
        approval_id: str = "",
        run_step_id: str | None = None,
    ) -> Any:
        """Invoke a tool through the gateway under the agent's contract."""
        agent.budget.check()
        allowed = set(agent.contract["tools"].get("allowed", []))
        if tool_key not in allowed:
            raise ContractViolation(
                f"agent '{agent.agent_key}' may not invoke tool '{tool_key}'",
                details={"allowed": sorted(allowed)},
            )
        agent_ctx = self.bind(ctx, agent)
        result = self._tools.invoke(
            agent_ctx,
            tool_key,
            parameters,
            idempotency_key=idempotency_key,
            approval_id=approval_id,
            run_step_id=run_step_id,
        )
        agent.budget.tool_calls_used += 1
        return result

    def _tool_call_count(self, ctx: ExecutionContext) -> int:
        if not ctx.run_id:
            return 0
        return int(
            self._session.execute(
                text("SELECT count(*) FROM tool_calls WHERE run_id = CAST(:r AS uuid)"),
                {"r": ctx.run_id},
            ).scalar_one()
        )

    @property
    def models(self) -> ModelGateway:
        return self._models

    @property
    def tools(self) -> ToolGateway:
        return self._tools

    @property
    def skills(self) -> SkillExecutor:
        return self._skills
