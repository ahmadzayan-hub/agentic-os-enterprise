"""Conductor.

Owns the governed execution path:

    objective -> intent -> plan -> validation -> risk -> policy -> approval
              -> dispatch -> skill/tool -> verification -> audit -> evidence

The Conductor reasons and sequences. It never executes a privileged tool
itself — its contract grants it none, and the plan validator rejects any plan
that gives it one (Architecture Constitution rule 17). Execution happens in the
agent the plan names, through the agent runtime, through the tool gateway.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.ai import prompt_registry
from agentic_os.ai.context_firewall import TrustTier
from agentic_os.assurance.audit import AuditEntry, AuditLedger
from agentic_os.control import risk_engine
from agentic_os.control.approval_engine import ApprovalCard, request_approval
from agentic_os.control.intent_router import Intent, interpret
from agentic_os.control.planner import Plan, Planner, ValidationResult, validate_plan
from agentic_os.control.policy_engine import PolicyEngine, PolicyRequest
from agentic_os.core.context import ExecutionContext
from agentic_os.core.errors import AgenticError, PolicyDenied, ValidationError
from agentic_os.core.ids import utcnow
from agentic_os.core.registry import load_registries
from agentic_os.runtime.agent_runtime import AgentRuntime
from agentic_os.runtime.events import Event, publish

CONDUCTOR_AGENT = "conductor"


@dataclass(slots=True)
class RunOutcome:
    run_id: str
    status: str
    objective: str
    intent: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    risk: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    tokens: int = 0
    error_class: str = ""
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "objective": self.objective,
            "intent": self.intent,
            "plan": self.plan,
            "validation": self.validation,
            "steps": self.steps,
            "result": self.result,
            "citations": self.citations,
            "approvals": self.approvals,
            "risk": self.risk,
            "cost_usd": round(self.cost_usd, 6),
            "tokens": self.tokens,
            "error_class": self.error_class,
            "error_message": self.error_message,
        }


class Conductor:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._runtime = AgentRuntime(session)
        self._ledger = AuditLedger(session)

    # -- public ------------------------------------------------------------
    def submit(
        self,
        ctx: ExecutionContext,
        objective: str,
        *,
        requested_autonomy: str = "A1",
        idempotency_key: str | None = None,
        dry_run: bool = False,
    ) -> RunOutcome:
        """Run the full governed path for one objective."""
        objective = (objective or "").strip()
        if len(objective) < 3:
            raise ValidationError("an objective must be at least 3 characters")

        intent = interpret(objective)
        run_id = self._create_run(ctx, objective, intent, requested_autonomy, idempotency_key)
        run_ctx = ctx.with_run(run_id)

        outcome = RunOutcome(run_id=run_id, status="PLANNING", objective=objective, intent=intent.to_dict())
        publish(
            self._session,
            run_ctx,
            Event(
                event_type="Run.Started",
                aggregate_type="run",
                aggregate_id=run_id,
                payload={"objective": objective[:500], "owner_agent": intent.owner_agent},
            ),
        )

        try:
            plan, validation = self._plan(run_ctx, intent)
            outcome.plan = plan.to_dict()
            outcome.validation = validation.to_dict()

            if not validation.valid:
                return self._fail(
                    run_ctx,
                    outcome,
                    "VALIDATION",
                    "the proposed plan did not pass validation",
                    status="FAILED",
                )

            self._persist_plan(run_ctx, run_id, plan, validation)

            gate = self._gate(run_ctx, intent, plan, validation)
            outcome.risk = gate["risk"]
            if gate["approvals"]:
                outcome.approvals = gate["approvals"]
                outcome.status = "AWAITING_APPROVAL"
                self._set_status(run_id, "AWAITING_APPROVAL")
                self._audit_run(run_ctx, run_id, "run.awaiting_approval", outcome)
                return outcome

            if dry_run:
                outcome.status = "PENDING"
                self._set_status(run_id, "PENDING")
                return outcome

            return self._execute(run_ctx, outcome, intent, plan)

        except AgenticError as exc:
            return self._fail(run_ctx, outcome, exc.error_class.value, exc.message)
        except Exception as exc:  # noqa: BLE001
            return self._fail(run_ctx, outcome, "INTERNAL", str(exc))

    # -- phases ------------------------------------------------------------
    def _plan(self, ctx: ExecutionContext, intent: Intent) -> tuple[Plan, ValidationResult]:
        conductor = self._runtime.open(ctx, CONDUCTOR_AGENT)
        conductor_ctx = self._runtime.bind(ctx, conductor)

        try:
            prompt = prompt_registry.resolve(self._session, ctx.tenant_id, "conductor.plan")
            system_prompt = prompt.body
        except Exception:
            system_prompt = "You are the Conductor. Propose a plan using only the supplied capabilities."

        # The objective is authenticated user input: analysable, never obeyed as
        # platform framing.
        self._runtime.add_context(
            conductor, intent.objective, TrustTier.AUTHENTICATED_USER_INPUT, source_ref="user"
        )

        planner = Planner(self._runtime.models)
        plan = planner.plan(
            conductor_ctx,
            intent,
            system_prompt=system_prompt,
            executing_agent=intent.owner_agent,
        )
        validation = validate_plan(plan, executing_agent=intent.owner_agent, intent=intent)
        return plan, validation

    def _gate(
        self, ctx: ExecutionContext, intent: Intent, plan: Plan, validation: ValidationResult
    ) -> dict[str, Any]:
        """Risk-classify and policy-check the plan; raise approvals if needed."""
        registries = load_registries()
        policy = PolicyEngine(self._session, ctx.tenant_id)
        approvals: list[str] = []

        highest = risk_engine.assess(
            risk_engine.RiskInput(
                action=f"plan:{intent.owner_agent}",
                side_effect="READ",
                classification=intent.classification,
                confidence=plan.confidence,
            )
        )

        for index in validation.requires_approval_steps:
            step = plan.steps[index]
            tool = registries.tools.get(step.tool, {}) if step.tool else {}
            assessment = risk_engine.assess(
                risk_engine.from_tool(
                    dict(tool) if tool else {"key": step.skill},
                    action=step.tool or step.skill,
                    classification=intent.classification,
                    confidence=plan.confidence,
                )
                if tool
                else risk_engine.RiskInput(
                    action=step.skill,
                    side_effect="WRITE",
                    classification=intent.classification,
                    confidence=plan.confidence,
                )
            )
            risk_engine.record(self._session, ctx, assessment, action=step.tool or step.skill)
            if risk_engine.RISK_ORDER.index(assessment.risk_class) > risk_engine.RISK_ORDER.index(
                highest.risk_class
            ):
                highest = assessment

            decision = policy.evaluate_and_record(
                ctx,
                PolicyRequest(
                    action="tool.invoke" if step.tool else "skill.execute",
                    resource=step.tool or step.skill,
                    attributes={
                        "side_effect": tool.get("side_effect", "WRITE"),
                        "reversibility": tool.get("reversibility", "REVERSIBLE"),
                        "classification": intent.classification,
                        "origin_trust_tier": "AUTHENTICATED_USER_INPUT",
                        "injection_detected": False,
                    },
                ),
            )
            if decision.effect == "DENY":
                raise PolicyDenied(decision.reason, details={"step": step.key})

            approval_id = request_approval(
                self._session,
                ctx,
                ApprovalCard(
                    action=step.tool or step.skill,
                    target=step.description[:200],
                    proposing_agent=intent.owner_agent,
                    autonomy_level=assessment.required_autonomy,
                    risk_class=assessment.risk_class,
                    financial_impact_usd=assessment.financial_impact_usd,
                    reversibility=assessment.reversibility,
                    confidence=plan.confidence,
                    reason=(f"Step {index + 1} of the plan for objective: {intent.objective[:300]}"),
                    consequences=(
                        f"Executing '{step.tool or step.skill}' has a "
                        f"{tool.get('side_effect', 'WRITE')} side effect and is "
                        f"{assessment.reversibility.lower()}."
                    ),
                    evidence=[{"plan_hash": plan.plan_hash, "step": step.to_dict()}],
                    sources=[{"type": "objective", "value": intent.objective[:300]}],
                    policy_refs=[
                        {"policy": m.policy_key, "rule": m.rule_name, "effect": m.effect}
                        for m in decision.matched
                    ],
                ),
                mode="DUAL" if assessment.risk_class == "CRITICAL" else "SINGLE",
                run_id=ctx.run_id,
            )
            approvals.append(approval_id)
            publish(
                self._session,
                ctx,
                Event(
                    event_type="Approval.Required",
                    aggregate_type="approval",
                    aggregate_id=approval_id,
                    payload={"action": step.tool or step.skill, "risk": assessment.risk_class},
                ),
            )

        self._session.execute(
            text(
                "UPDATE runs SET risk_class = CAST(:rc AS risk_class), risk_score = :rs, "
                "confidence = :conf WHERE id = :i"
            ),
            {"rc": highest.risk_class, "rs": highest.score, "conf": plan.confidence, "i": ctx.run_id},
        )
        return {"risk": highest.to_dict(), "approvals": approvals}

    def _execute(self, ctx: ExecutionContext, outcome: RunOutcome, intent: Intent, plan: Plan) -> RunOutcome:
        agent = self._runtime.open(ctx, intent.owner_agent)
        self._set_status(outcome.run_id, "RUNNING", started=True)
        outcome.status = "RUNNING"

        state: dict[str, Any] = {}
        for step in plan.steps:
            step_id = self._create_step(ctx, outcome.run_id, step)
            params = self._materialise_params(step, intent, state)
            started = utcnow()
            try:
                result = self._runtime.run_skill(
                    ctx, agent, step.skill, params, idempotency_key=f"{outcome.run_id}:{step.index}"
                )
            except AgenticError as exc:
                self._complete_step(
                    step_id, "FAILED", None, error_class=exc.error_class.value, error=exc.message
                )
                outcome.steps.append(
                    {
                        "index": step.index,
                        "key": step.key,
                        "skill": step.skill,
                        "status": "FAILED",
                        "error": exc.message,
                    }
                )
                return self._fail(ctx, outcome, exc.error_class.value, exc.message)

            state[step.key] = result.output
            self._complete_step(
                step_id,
                "SUCCEEDED",
                result.output,
                cost=result.cost_usd,
                tokens=(result.input_tokens, result.output_tokens),
            )
            outcome.steps.append(
                {
                    "index": step.index,
                    "key": step.key,
                    "skill": step.skill,
                    "status": "SUCCEEDED",
                    "deterministic": result.deterministic,
                    "model_key": result.model_key,
                    "cost_usd": result.cost_usd,
                    "confidence": result.confidence,
                    "citations": result.citations,
                    "latency_ms": int((utcnow() - started).total_seconds() * 1000),
                    "output": result.output,
                }
            )
            outcome.cost_usd += result.cost_usd
            outcome.tokens += result.input_tokens + result.output_tokens

        outcome.citations = agent.citations
        outcome.result = self._consolidate(state, plan)
        outcome.status = "SUCCEEDED"
        self._finish_run(ctx, outcome)
        return outcome

    # -- helpers -----------------------------------------------------------
    def _materialise_params(self, step: Any, intent: Intent, state: dict[str, Any]) -> dict[str, Any]:
        """Build skill inputs from the objective and prior step outputs."""
        if step.skill == "search":
            return {"query": intent.objective, "top_k": 8}
        if step.skill in ("summarise", "analyse"):
            sources: list[dict[str, Any]] = []
            for value in state.values():
                for result in (value or {}).get("results", []):
                    sources.append({"id": result["chunk_id"], "text": result.get("snippet", "")})
            if not sources:
                sources = [{"id": "objective", "text": intent.objective}]
            if step.skill == "summarise":
                return {"sources": sources, "max_words": 220}
            return {
                "subject": intent.objective[:200],
                "evidence": [{"id": s["id"], "text": s["text"]} for s in sources],
                "question": intent.objective,
            }
        if step.skill == "classify":
            return {
                "text": intent.objective,
                "labels": ["operational", "financial", "safety", "administrative"],
            }
        if step.skill == "validate":
            return {"payload": state, "schema": {"type": "object"}}
        if step.skill == "verify":
            claims = []
            sources = []
            for value in state.values():
                for finding in (value or {}).get("findings", []):
                    claims.append(
                        {"statement": finding["statement"], "citations": finding.get("support", [])}
                    )
                for result in (value or {}).get("results", []):
                    sources.append({"id": result["chunk_id"], "text": result.get("snippet", "")})
            return {"claims": claims, "sources": sources, "min_overlap": 0.4}
        if step.skill == "draft":
            evidence = []
            for value in state.values():
                if summary := (value or {}).get("summary"):
                    evidence.append({"id": "summary", "statement": summary})
            return {
                "document_type": "briefing",
                "context": intent.objective,
                "evidence": evidence or [{"id": "objective", "statement": intent.objective}],
            }
        if step.skill == "extract":
            return {"text": intent.objective, "fields": list(intent.entities) or ["subject"]}

        # A step whose inputs cannot be built from the objective and prior state
        # must fail here, naming the skill, rather than reaching the skill and
        # producing an opaque schema error.
        raise ValidationError(
            f"the plan proposes skill '{step.skill}' but the Conductor cannot build its "
            f"inputs from this objective",
            details={"skill": step.skill, "step": step.key},
        )

    @staticmethod
    def _consolidate(state: dict[str, Any], plan: Plan) -> dict[str, Any]:
        """Assemble the final answer from step outputs, preserving provenance."""
        answer = ""
        citations: list[str] = []
        for step in reversed(plan.steps):
            output = state.get(step.key) or {}
            if summary := output.get("summary"):
                answer = summary
                citations = output.get("citations", [])
                break
            if findings := output.get("findings"):
                answer = " ".join(f["statement"] for f in findings)
                citations = [c for f in findings for c in f.get("support", [])]
                break
            if draft := output.get("draft"):
                answer = draft
                break
        return {
            "answer": answer,
            "citations": sorted(set(citations)),
            "grounded": bool(citations),
            "steps_executed": len(plan.steps),
            "state": state,
        }

    def _create_run(
        self,
        ctx: ExecutionContext,
        objective: str,
        intent: Intent,
        requested_autonomy: str,
        idempotency_key: str | None,
    ) -> str:
        if idempotency_key:
            existing = self._session.execute(
                text("SELECT id FROM runs WHERE tenant_id = :t AND idempotency_key = :i"),
                {"t": ctx.tenant_id, "i": idempotency_key},
            ).first()
            if existing is not None:
                return str(existing.id)

        row = self._session.execute(
            text(
                """
                INSERT INTO runs (tenant_id, organization_id, correlation_id, idempotency_key,
                                  objective, intent, requested_by, owner_agent_key, status,
                                  autonomy_level, classification, deadline_at)
                VALUES (:t, :o, :corr, :idem, :obj, :intent, :user, :agent, 'PLANNING',
                        CAST(:aut AS autonomy_level), CAST(:cls AS data_classification),
                        now() + interval '1 hour')
                RETURNING id
                """
            ),
            {
                "t": ctx.tenant_id,
                "o": ctx.organization_id,
                "corr": ctx.correlation_id,
                "idem": idempotency_key,
                "obj": objective,
                "intent": intent.task_kind,
                "user": ctx.human.user_id if ctx.human else None,
                "agent": intent.owner_agent,
                "aut": requested_autonomy,
                "cls": intent.classification,
            },
        ).one()
        return str(row.id)

    def _persist_plan(
        self, ctx: ExecutionContext, run_id: str, plan: Plan, validation: ValidationResult
    ) -> None:
        self._session.execute(
            text(
                """
                INSERT INTO plans (tenant_id, run_id, version, planner, steps, plan_hash,
                                   validated, validation_errors, rationale, estimated_cost_usd)
                VALUES (:t, :r, 1, :planner, CAST(:steps AS jsonb), :hash, :valid,
                        CAST(:errors AS jsonb), :rationale, :cost)
                ON CONFLICT (run_id, version) DO UPDATE
                  SET steps = EXCLUDED.steps, plan_hash = EXCLUDED.plan_hash,
                      validated = EXCLUDED.validated,
                      validation_errors = EXCLUDED.validation_errors
                """
            ),
            {
                "t": ctx.tenant_id,
                "r": run_id,
                "planner": plan.planner,
                "steps": json.dumps([s.to_dict() for s in plan.steps], default=str),
                "hash": plan.plan_hash,
                "valid": validation.valid,
                "errors": json.dumps([i.to_dict() for i in validation.issues], default=str),
                "rationale": plan.rationale,
                "cost": plan.estimated_cost_usd,
            },
        )

    def _create_step(self, ctx: ExecutionContext, run_id: str, step: Any) -> str:
        row = self._session.execute(
            text(
                """
                INSERT INTO run_steps (tenant_id, run_id, step_index, step_key, step_type,
                                       agent_key, skill_key, tool_key, status, idempotency_key,
                                       input, started_at)
                VALUES (:t, :r, :i, :k, :type, :agent, :skill, :tool, 'RUNNING', :idem,
                        CAST(:input AS jsonb), now())
                RETURNING id
                """
            ),
            {
                "t": ctx.tenant_id,
                "r": run_id,
                "i": step.index,
                "k": step.key,
                "type": "TOOL" if step.tool else "SKILL",
                "agent": step.agent,
                "skill": step.skill,
                "tool": step.tool,
                "idem": f"{run_id}:{step.index}",
                "input": json.dumps(step.to_dict(), default=str),
            },
        ).one()
        return str(row.id)

    def _complete_step(
        self,
        step_id: str,
        status: str,
        output: dict | None,
        *,
        error_class: str = "",
        error: str = "",
        cost: float = 0.0,
        tokens: tuple[int, int] = (0, 0),
    ) -> None:
        self._session.execute(
            text(
                """
                UPDATE run_steps
                   SET status = CAST(:s AS run_status), output = CAST(:o AS jsonb),
                       error_class = :ec, error_message = :em, cost_usd = :cost,
                       input_tokens = :it, output_tokens = :ot, completed_at = now(),
                       latency_ms = EXTRACT(EPOCH FROM (now() - started_at)) * 1000
                 WHERE id = :i
                """
            ),
            {
                "s": status,
                "o": json.dumps(output, default=str) if output is not None else None,
                "ec": error_class,
                "em": error[:2000],
                "cost": cost,
                "it": tokens[0],
                "ot": tokens[1],
                "i": step_id,
            },
        )

    def _set_status(self, run_id: str, status: str, *, started: bool = False) -> None:
        self._session.execute(
            text(
                "UPDATE runs SET status = CAST(:s AS run_status), "
                "started_at = COALESCE(started_at, CASE WHEN :started THEN now() END), "
                "updated_at = now() WHERE id = :i"
            ),
            {"s": status, "started": started, "i": run_id},
        )

    def _finish_run(self, ctx: ExecutionContext, outcome: RunOutcome) -> None:
        self._session.execute(
            text(
                """
                UPDATE runs
                   SET status = 'SUCCEEDED', result = CAST(:result AS jsonb),
                       cost_usd = :cost, input_tokens = :it, output_tokens = :ot,
                       completed_at = now(),
                       duration_ms = EXTRACT(EPOCH FROM (now() - COALESCE(started_at, created_at))) * 1000,
                       updated_at = now()
                 WHERE id = :i
                """
            ),
            {
                "result": json.dumps(outcome.result, default=str),
                "cost": outcome.cost_usd,
                "it": outcome.tokens,
                "ot": 0,
                "i": outcome.run_id,
            },
        )
        for citation in outcome.result.get("citations", []) if outcome.result else []:
            self._session.execute(
                text(
                    "INSERT INTO citations (tenant_id, run_id, chunk_id, verified) "
                    "VALUES (:t, :r, CAST(:c AS uuid), true) ON CONFLICT DO NOTHING"
                ),
                {"t": ctx.tenant_id, "r": outcome.run_id, "c": citation},
            )
        publish(
            self._session,
            ctx,
            Event(
                event_type="Run.Completed",
                aggregate_type="run",
                aggregate_id=outcome.run_id,
                payload={"cost_usd": outcome.cost_usd, "steps": len(outcome.steps)},
            ),
        )
        self._audit_run(ctx, outcome.run_id, "run.completed", outcome)

    def _fail(
        self,
        ctx: ExecutionContext,
        outcome: RunOutcome,
        error_class: str,
        message: str,
        *,
        status: str = "FAILED",
    ) -> RunOutcome:
        outcome.status = status
        outcome.error_class = error_class
        outcome.error_message = message
        self._session.execute(
            text(
                "UPDATE runs SET status = CAST(:s AS run_status), error_class = :ec, "
                "error_message = :em, completed_at = now(), updated_at = now() WHERE id = :i"
            ),
            {"s": status, "ec": error_class, "em": message[:2000], "i": outcome.run_id},
        )
        publish(
            self._session,
            ctx,
            Event(
                event_type="Run.Failed",
                aggregate_type="run",
                aggregate_id=outcome.run_id,
                payload={"error_class": error_class, "error": message[:500]},
            ),
        )
        self._audit_run(ctx, outcome.run_id, "run.failed", outcome, outcome_status="FAILURE")
        return outcome

    def _audit_run(
        self,
        ctx: ExecutionContext,
        run_id: str,
        action: str,
        outcome: RunOutcome,
        *,
        outcome_status: str = "SUCCESS",
    ) -> None:
        self._ledger.append(
            ctx,
            AuditEntry(
                category="AGENT_ACTION",
                action=action,
                outcome=outcome_status,  # type: ignore[arg-type]
                resource_type="run",
                resource_id=run_id,
                payload={
                    "objective": outcome.objective[:500],
                    "owner_agent": outcome.intent.get("owner_agent"),
                    "status": outcome.status,
                    "plan_hash": outcome.plan.get("plan_hash"),
                    "steps": len(outcome.steps),
                    "cost_usd": outcome.cost_usd,
                    "risk_class": outcome.risk.get("risk_class"),
                    "approvals": outcome.approvals,
                    "error": outcome.error_message[:500],
                },
            ),
        )
