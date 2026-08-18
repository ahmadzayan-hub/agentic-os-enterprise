"""Planner and Plan Validator.

The planner proposes steps; the validator decides whether they may exist at
all. The split matters: a plan produced by a model is an untrusted proposal,
and the validator is the deterministic gate that rejects anything referencing a
capability the agent does not hold, exceeding budget, or attempting an action
the caller cannot authorise.

A plan that fails validation is never partially executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentic_os.ai.model_gateway import ModelGateway
from agentic_os.ai.providers import ModelRequest
from agentic_os.control.intent_router import Intent
from agentic_os.core.context import ExecutionContext
from agentic_os.core.crypto import content_hash
from agentic_os.core.errors import ValidationError
from agentic_os.core.registry import load_registries

MAX_STEPS = 12


@dataclass(slots=True)
class PlanStep:
    index: int
    key: str
    agent: str
    skill: str = ""
    tool: str = ""
    description: str = ""
    requires_approval: bool = False
    produces: str = ""
    depends_on: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "key": self.key,
            "agent": self.agent,
            "skill": self.skill,
            "tool": self.tool,
            "description": self.description,
            "requires_approval": self.requires_approval,
            "produces": self.produces,
            "depends_on": self.depends_on,
        }


@dataclass(slots=True)
class Plan:
    objective: str
    steps: list[PlanStep]
    rationale: str = ""
    planner: str = "conductor.planner"
    estimated_cost_usd: float = 0.0
    confidence: float = 0.0
    model_key: str = ""

    @property
    def plan_hash(self) -> str:
        return content_hash([s.to_dict() for s in self.steps])

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "steps": [s.to_dict() for s in self.steps],
            "rationale": self.rationale,
            "planner": self.planner,
            "estimated_cost_usd": self.estimated_cost_usd,
            "confidence": self.confidence,
            "model_key": self.model_key,
            "plan_hash": self.plan_hash,
        }


@dataclass(slots=True)
class ValidationIssue:
    step_index: int | None
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"step_index": self.step_index, "code": self.code, "message": self.message}


@dataclass(slots=True)
class ValidationResult:
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    requires_approval_steps: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": [i.to_dict() for i in self.issues],
            "requires_approval_steps": self.requires_approval_steps,
        }


def capabilities_for(agent_key: str) -> dict[str, Any]:
    """The skills, tools and models an agent's contract actually grants."""
    registries = load_registries()
    contract = registries.agents.get(agent_key)
    if contract is None:
        raise ValidationError(f"unknown agent '{agent_key}'")
    return {
        "agent": agent_key,
        "skills": list(contract["skills"]["allowed"]),
        "tools": list(contract["tools"].get("allowed", [])),
        "models": list(contract["models"]["allowed"]),
        "max_autonomy": contract["autonomy"]["max_level"],
        "max_tool_calls": contract["limits"]["max_tool_calls"],
        "cost_budget_usd": contract["limits"]["cost_budget_usd"],
    }


class Planner:
    """Produces a plan for an intent, through the model gateway."""

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    def plan(
        self, ctx: ExecutionContext, intent: Intent, *, system_prompt: str, executing_agent: str
    ) -> Plan:
        capabilities = capabilities_for(executing_agent)
        result = self._gateway.complete(
            ctx,
            ModelRequest(
                system=system_prompt,
                user=intent.objective,
                task_kind="plan",
                response_format="json",
                payload={"objective": intent.objective, "capabilities": capabilities},
            ),
            classification=intent.classification,
            complexity=intent.complexity,
        )
        body = result.response.structured or result.response.as_json()

        steps: list[PlanStep] = []
        for index, raw in enumerate(body.get("steps", [])[:MAX_STEPS]):
            steps.append(
                PlanStep(
                    index=index,
                    key=str(raw.get("key") or f"step-{index + 1}"),
                    agent=str(raw.get("agent") or executing_agent),
                    skill=str(raw.get("skill") or ""),
                    tool=str(raw.get("tool") or "" or ""),
                    description=str(raw.get("description") or ""),
                    requires_approval=bool(raw.get("requires_approval", False)),
                    produces=str(raw.get("produces") or ""),
                    depends_on=[int(d) for d in raw.get("depends_on", []) if str(d).isdigit()],
                )
            )
        return Plan(
            objective=intent.objective,
            steps=steps,
            rationale=str(body.get("rationale", "")),
            estimated_cost_usd=result.cost_usd,
            confidence=float(body.get("confidence", 0.0)),
            model_key=result.routing.model_key,
        )


def validate_plan(
    plan: Plan,
    *,
    executing_agent: str,
    intent: Intent | None = None,
    max_steps: int = MAX_STEPS,
) -> ValidationResult:
    """Deterministic gate. Rejects any plan that could not lawfully execute."""
    registries = load_registries()
    issues: list[ValidationIssue] = []
    approval_steps: list[int] = []

    if not plan.steps:
        issues.append(
            ValidationIssue(
                None,
                "EMPTY_PLAN",
                "the planner produced no executable steps for this objective",
            )
        )

    if len(plan.steps) > max_steps:
        issues.append(
            ValidationIssue(
                None, "TOO_MANY_STEPS", f"plan has {len(plan.steps)} steps, limit is {max_steps}"
            )
        )

    contract = registries.agents.get(executing_agent)
    if contract is None:
        issues.append(
            ValidationIssue(None, "UNKNOWN_AGENT", f"no contract for agent '{executing_agent}'")
        )
        return ValidationResult(valid=False, issues=issues)

    allowed_skills = set(contract["skills"]["allowed"])
    allowed_tools = set(contract["tools"].get("allowed", []))
    denied_tools = set(contract["tools"].get("denied", []))
    max_tool_calls = int(contract["limits"]["max_tool_calls"])

    seen_keys: set[str] = set()
    tool_call_count = 0

    for step in plan.steps:
        if step.key in seen_keys:
            issues.append(ValidationIssue(step.index, "DUPLICATE_KEY", f"duplicate step key '{step.key}'"))
        seen_keys.add(step.key)

        if step.agent != executing_agent:
            issues.append(
                ValidationIssue(
                    step.index,
                    "AGENT_MISMATCH",
                    f"step names agent '{step.agent}' but the run is dispatched to "
                    f"'{executing_agent}'",
                )
            )

        if not step.skill and not step.tool:
            issues.append(
                ValidationIssue(step.index, "NO_CAPABILITY", "step names neither a skill nor a tool")
            )

        if step.skill:
            if step.skill not in registries.skills:
                issues.append(
                    ValidationIssue(
                        step.index, "UNKNOWN_SKILL", f"skill '{step.skill}' is not registered"
                    )
                )
            elif step.skill not in allowed_skills:
                issues.append(
                    ValidationIssue(
                        step.index,
                        "SKILL_NOT_PERMITTED",
                        f"agent '{executing_agent}' may not use skill '{step.skill}'",
                    )
                )

        if step.tool:
            tool_call_count += 1
            if step.tool not in registries.tools:
                issues.append(
                    ValidationIssue(
                        step.index, "UNKNOWN_TOOL", f"tool '{step.tool}' is not registered"
                    )
                )
            elif step.tool in denied_tools or "*" in denied_tools:
                issues.append(
                    ValidationIssue(
                        step.index,
                        "TOOL_DENIED",
                        f"agent '{executing_agent}' is explicitly denied tool '{step.tool}'",
                    )
                )
            elif step.tool not in allowed_tools:
                issues.append(
                    ValidationIssue(
                        step.index,
                        "TOOL_NOT_PERMITTED",
                        f"agent '{executing_agent}' may not invoke tool '{step.tool}'",
                    )
                )
            else:
                tool = registries.tools[step.tool]
                if tool["implementation_status"] == "NOT_IMPLEMENTED":
                    issues.append(
                        ValidationIssue(
                            step.index,
                            "TOOL_NOT_IMPLEMENTED",
                            f"tool '{step.tool}' is declared but not implemented",
                        )
                    )
                if tool["side_effect"] in ("FINANCIAL", "DELETE", "EXTERNAL") or tool.get(
                    "requires_approval"
                ):
                    approval_steps.append(step.index)
                    if not step.requires_approval:
                        issues.append(
                            ValidationIssue(
                                step.index,
                                "APPROVAL_NOT_DECLARED",
                                f"tool '{step.tool}' has a {tool['side_effect']} side effect but "
                                "the step does not declare requires_approval",
                            )
                        )

        for dependency in step.depends_on:
            if dependency >= step.index:
                issues.append(
                    ValidationIssue(
                        step.index,
                        "FORWARD_DEPENDENCY",
                        f"step depends on step {dependency}, which does not precede it",
                    )
                )
            if dependency < 0 or dependency >= len(plan.steps):
                issues.append(
                    ValidationIssue(
                        step.index, "UNKNOWN_DEPENDENCY", f"step depends on missing step {dependency}"
                    )
                )

        if step.requires_approval and step.index not in approval_steps:
            approval_steps.append(step.index)

    if max_tool_calls == 0 and tool_call_count > 0:
        issues.append(
            ValidationIssue(
                None,
                "TOOL_BUDGET_ZERO",
                f"agent '{executing_agent}' holds no tool authority but the plan makes "
                f"{tool_call_count} tool calls",
            )
        )
    elif tool_call_count > max_tool_calls:
        issues.append(
            ValidationIssue(
                None,
                "TOOL_BUDGET_EXCEEDED",
                f"plan makes {tool_call_count} tool calls, contract allows {max_tool_calls}",
            )
        )

    if intent is not None and intent.consequential and not approval_steps:
        issues.append(
            ValidationIssue(
                None,
                "CONSEQUENTIAL_WITHOUT_APPROVAL",
                "the objective describes a consequential action but no step requires approval",
            )
        )

    return ValidationResult(
        valid=not issues, issues=issues, requires_approval_steps=sorted(set(approval_steps))
    )
