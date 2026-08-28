"""Tool Security Gateway.

Every tool call passes through the same ordered pipeline. The order matters:
cheap identity and authorization checks run before anything expensive, and no
credential is resolved until the call is fully authorised.

     1  identity            8  parameter schema validation
     2  kill switches       9  idempotency
     3  tenant validation  10  credential injection
     4  authorization      11  execution
     5  tool resolution    12  output sanitization
     6  policy             13  side-effect verification
     7  risk + approval    14  audit + evidence

Every rejection records which stage produced it, so a denial is diagnosable
rather than a generic 403.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentic_os.ai.model_gateway import kill_switch_engaged
from agentic_os.assurance.audit import AuditEntry, AuditLedger, redact_payload
from agentic_os.control import risk_engine
from agentic_os.control.policy_engine import PolicyEngine, PolicyRequest
from agentic_os.core.context import ExecutionContext, ToolIdentity, classification_rank
from agentic_os.core.crypto import content_hash
from agentic_os.core.errors import (
    AgenticError,
    ApprovalRequired,
    AuthorizationError,
    KillSwitchEngaged,
    NotFound,
    NotImplementedCapability,
    PolicyDenied,
    ValidationError,
)
from agentic_os.core.registry import load_registries
from agentic_os.identity.authz import (
    AgentAuthorizationProfile,
    AuthorizationRequest,
    Resource,
    authorize,
)
from agentic_os.tools.builtin import BUILTIN_TOOLS

STAGES = (
    "IDENTITY",
    "KILL_SWITCH",
    "TENANT",
    "AUTHORIZATION",
    "TOOL_RESOLUTION",
    "POLICY",
    "RISK",
    "APPROVAL",
    "PARAMETERS",
    "IDEMPOTENCY",
    "CREDENTIALS",
    "EXECUTION",
    "SANITIZATION",
    "VERIFICATION",
)


@dataclass(slots=True)
class ToolCallResult:
    tool_key: str
    decision: str  # ALLOWED | DENIED | APPROVAL_REQUIRED | ERROR
    result: dict[str, Any] | None = None
    denial_stage: str = ""
    denial_reason: str = ""
    approval_id: str = ""
    latency_ms: int = 0
    risk: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    verification: str = "NOT_APPLICABLE"
    idempotent_replay: bool = False
    call_id: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == "ALLOWED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_key": self.tool_key,
            "decision": self.decision,
            "denial_stage": self.denial_stage,
            "denial_reason": self.denial_reason,
            "approval_id": self.approval_id,
            "latency_ms": self.latency_ms,
            "risk": self.risk,
            "policy": self.policy,
            "verification": self.verification,
            "idempotent_replay": self.idempotent_replay,
            "result": self.result,
        }


class ToolGateway:
    """The only path from an agent to any tool."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._ledger = AuditLedger(session)

    # -- public ------------------------------------------------------------
    def invoke(
        self,
        ctx: ExecutionContext,
        tool_key: str,
        parameters: dict[str, Any],
        *,
        idempotency_key: str,
        run_step_id: str | None = None,
        approval_id: str = "",
        raise_on_denial: bool = True,
    ) -> ToolCallResult:
        started = time.perf_counter()
        result = ToolCallResult(tool_key=tool_key, decision="DENIED")
        try:
            result = self._invoke_inner(ctx, tool_key, parameters, idempotency_key, run_step_id, approval_id)
        except AgenticError as exc:
            result = ToolCallResult(
                tool_key=tool_key,
                decision="APPROVAL_REQUIRED" if isinstance(exc, ApprovalRequired) else "DENIED",
                denial_stage=str(exc.details.get("stage", "EXECUTION")),
                denial_reason=exc.message,
                approval_id=str(exc.details.get("approval_id", "")),
            )
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            self._record(ctx, result, parameters, idempotency_key, run_step_id)
            if raise_on_denial:
                raise
            return result

        result.latency_ms = int((time.perf_counter() - started) * 1000)
        if not result.idempotent_replay:
            self._record(ctx, result, parameters, idempotency_key, run_step_id)
        return result

    # -- pipeline ----------------------------------------------------------
    def _invoke_inner(
        self,
        ctx: ExecutionContext,
        tool_key: str,
        parameters: dict[str, Any],
        idempotency_key: str,
        run_step_id: str | None,
        approval_id: str,
    ) -> ToolCallResult:
        # 1 identity ---------------------------------------------------------
        if ctx.human is None and not ctx.service_principal:
            raise AuthorizationError(
                "tool invocation requires an authenticated principal",
                details={"stage": "IDENTITY"},
            )

        # 2 kill switches ----------------------------------------------------
        for scope, target in (
            ("GLOBAL", ""),
            ("TENANT", ""),
            ("TOOL", tool_key),
            ("AGENT", ctx.agent.agent_id if ctx.agent else ""),
        ):
            if scope == "AGENT" and not target:
                continue
            if kill_switch_engaged(self._session, ctx.tenant_id, scope, target):
                raise KillSwitchEngaged(
                    f"{scope} kill switch is engaged" + (f" for '{target}'" if target else ""),
                    details={"stage": "KILL_SWITCH", "scope": scope, "target": target},
                )

        # 5 tool resolution (needed before policy/risk can be evaluated) -----
        tool = self._resolve_tool(ctx, tool_key)
        if tool["implementation_status"] == "NOT_IMPLEMENTED" or tool_key not in BUILTIN_TOOLS:
            raise NotImplementedCapability(
                f"tool '{tool_key}' is registered but not implemented; it cannot be executed",
                details={"stage": "TOOL_RESOLUTION", "tool_key": tool_key},
            )
        if tool["status"] != "ACTIVE":
            raise PolicyDenied(
                f"tool '{tool_key}' is {tool['status']}",
                details={"stage": "TOOL_RESOLUTION"},
            )

        # 3 tenant + 4 authorization ------------------------------------------
        # A tool's max_classification is a *capability ceiling* — the most
        # sensitive data the tool is cleared to handle — not a clearance the
        # caller must hold. The clearance check therefore uses the
        # classification of the data this call will touch, and the ceiling is
        # enforced separately below.
        request_classification = str(ctx.attributes.get("classification", "INTERNAL"))
        if classification_rank(request_classification) > classification_rank(str(tool["max_classification"])):
            raise PolicyDenied(
                f"tool '{tool_key}' is cleared to handle at most "
                f"{tool['max_classification']} data, but this call carries "
                f"{request_classification}",
                details={"stage": "AUTHORIZATION"},
            )

        agent_profile = self._agent_profile(ctx)
        tool_ctx = ctx.with_tool(
            ToolIdentity(
                tool_id=tool_key,
                connector_id=tool["connector_key"] or "",
                scopes=frozenset(tool["scopes"] or []),
            )
        )
        decision = authorize(
            tool_ctx,
            AuthorizationRequest(
                action="tools:invoke",
                resource=Resource(
                    "tool",
                    tool_key,
                    tenant_id=ctx.tenant_id,
                    classification=request_classification,
                ),
                required_autonomy=str(tool["min_autonomy"]),
                required_scopes=frozenset(tool["scopes"] or []),
                risk_class=str(tool["risk_class"]),
            ),
            agent_profile=agent_profile,
        )
        if not decision.allowed:
            raise AuthorizationError(
                decision.reason,
                details={"stage": "AUTHORIZATION", "failed_check": decision.failed_stage},
            )

        # 6 policy -----------------------------------------------------------
        policy = PolicyEngine(self._session, ctx.tenant_id)
        policy_request = PolicyRequest(
            action="tool.invoke",
            resource=tool_key,
            attributes={
                "side_effect": tool["side_effect"],
                "reversibility": tool["reversibility"],
                "classification": request_classification,
                "tool_max_classification": str(tool["max_classification"]),
                "tool_key": tool_key,
                "origin_trust_tier": ctx.attributes.get("origin_trust_tier", "SYSTEM_TRUSTED"),
                "injection_detected": bool(ctx.attributes.get("injection_detected", False)),
                "amount_usd": float(parameters.get("amount_usd", 0) or 0),
                "mcp_trust_class": tool.get("mcp_trust_class", ""),
            },
        )
        policy_decision = policy.evaluate_and_record(ctx, policy_request, run_step_id=run_step_id)
        if policy_decision.effect == "DENY":
            raise PolicyDenied(
                policy_decision.reason,
                details={"stage": "POLICY", "policy": policy_decision.to_dict()},
            )

        # 7 risk --------------------------------------------------------------
        assessment = risk_engine.assess(
            risk_engine.from_tool(
                dict(tool),
                action=tool_key,
                classification=request_classification,
                financial_impact_usd=float(parameters.get("amount_usd", 0) or 0),
                injection_detected=bool(ctx.attributes.get("injection_detected", False)),
                origin_trust_tier=str(ctx.attributes.get("origin_trust_tier", "SYSTEM_TRUSTED")),
                confidence=ctx.attributes.get("confidence"),
            )
        )
        risk_engine.record(self._session, ctx, assessment, action=tool_key, run_step_id=run_step_id)

        # 8 approval ----------------------------------------------------------
        needs_approval = (
            policy_decision.requires_approval
            or assessment.requires_approval
            or bool(tool["requires_approval"])
            or "REQUIRE_HUMAN_APPROVAL" in decision.obligations
        )
        if needs_approval and not self._approval_satisfied(ctx, approval_id, tool_key):
            raise ApprovalRequired(
                f"tool '{tool_key}' requires human authorisation before execution",
                details={
                    "stage": "APPROVAL",
                    "risk": assessment.to_dict(),
                    "policy": policy_decision.to_dict(),
                    "approval_id": approval_id,
                },
            )

        # 9 parameter schema validation ---------------------------------------
        self._validate_parameters(tool, parameters)

        # 10 idempotency -------------------------------------------------------
        replay = self._existing_call(ctx, tool_key, idempotency_key)
        if replay is not None:
            return ToolCallResult(
                tool_key=tool_key,
                decision=str(replay["gateway_decision"]),
                result=replay["result_redacted"],
                risk=assessment.to_dict(),
                policy=policy_decision.to_dict(),
                verification=str(replay["verification_status"]),
                idempotent_replay=True,
                call_id=str(replay["id"]),
            )

        # 11 credentials + 12 execution ---------------------------------------
        started = time.perf_counter()
        handler = BUILTIN_TOOLS[tool_key]
        try:
            raw_result = handler(self._session, tool_ctx, parameters)
        except AgenticError:
            raise
        except Exception as exc:  # noqa: BLE001 - converted to a typed error
            raise ValidationError(f"tool '{tool_key}' failed: {exc}", details={"stage": "EXECUTION"}) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        # 13 output sanitization ----------------------------------------------
        sanitised = self._sanitise(raw_result)

        # 14 side-effect verification -----------------------------------------
        verification = self._verify(tool, tool_ctx, sanitised)

        return ToolCallResult(
            tool_key=tool_key,
            decision="ALLOWED",
            result=sanitised,
            latency_ms=latency_ms,
            risk=assessment.to_dict(),
            policy=policy_decision.to_dict(),
            verification=verification,
        )

    # -- helpers -----------------------------------------------------------
    def _resolve_tool(self, ctx: ExecutionContext, tool_key: str) -> dict[str, Any]:
        row = (
            self._session.execute(
                text("SELECT * FROM tools WHERE tenant_id = :t AND tool_key = :k"),
                {"t": ctx.tenant_id, "k": tool_key},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound(
                f"tool '{tool_key}' is not registered in this tenant",
                details={"stage": "TOOL_RESOLUTION"},
            )
        return dict(row)

    def _agent_profile(self, ctx: ExecutionContext) -> AgentAuthorizationProfile | None:
        if ctx.agent is None:
            return None
        contract = load_registries().agents.get(ctx.agent.agent_id)
        if contract is None:
            raise AuthorizationError(
                f"agent '{ctx.agent.agent_id}' has no registered contract",
                details={"stage": "AUTHORIZATION"},
            )
        return AgentAuthorizationProfile(
            agent_key=ctx.agent.agent_id,
            max_autonomy=contract["autonomy"]["max_level"],
            allowed_tools=frozenset(contract["tools"].get("allowed", [])),
            allowed_skills=frozenset(contract["skills"]["allowed"]),
            allowed_models=frozenset(contract["models"]["allowed"]),
            permitted_domains=frozenset(contract["data"]["permitted_domains"]),
            prohibited_domains=frozenset(contract["data"]["prohibited_domains"]),
            max_classification=contract["data"]["max_classification"],
        )

    def _validate_parameters(self, tool: dict[str, Any], parameters: dict[str, Any]) -> None:
        schema = tool.get("parameter_schema") or {}
        if isinstance(schema, str):
            schema = json.loads(schema)
        if not schema:
            return
        errors = sorted(Draft202012Validator(schema).iter_errors(parameters), key=lambda e: list(e.path))
        if errors:
            raise ValidationError(
                f"parameters for '{tool['tool_key']}' failed schema validation",
                details={
                    "stage": "PARAMETERS",
                    "errors": [{"path": list(e.path), "message": e.message} for e in errors[:10]],
                },
            )

    def _approval_satisfied(self, ctx: ExecutionContext, approval_id: str, tool_key: str) -> bool:
        if not approval_id:
            return False
        row = (
            self._session.execute(
                text(
                    "SELECT status, action, expires_at FROM approvals "
                    "WHERE tenant_id = :t AND id = CAST(:i AS uuid)"
                ),
                {"t": ctx.tenant_id, "i": approval_id},
            )
            .mappings()
            .first()
        )
        if row is None or row["status"] != "APPROVED":
            return False
        # An approval authorises one action, not any action.
        return row["action"] in (tool_key, f"tool.invoke:{tool_key}")

    def _existing_call(
        self, ctx: ExecutionContext, tool_key: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        row = (
            self._session.execute(
                text(
                    "SELECT id, gateway_decision, result_redacted, verification_status "
                    "FROM tool_calls WHERE tenant_id = :t AND tool_key = :k AND idempotency_key = :i"
                ),
                {"t": ctx.tenant_id, "k": tool_key, "i": idempotency_key},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    @staticmethod
    def _sanitise(result: dict[str, Any]) -> dict[str, Any]:
        """Strip anything secret-shaped from a tool result before it is returned.

        Tool output flows into a model context, so it is treated as untrusted:
        the caller is responsible for wrapping it with the context firewall, and
        the gateway guarantees it carries no credentials.
        """
        return redact_payload(result)

    def _verify(self, tool: dict[str, Any], ctx: ExecutionContext, result: dict[str, Any]) -> str:
        """Confirm the side effect actually happened, where the tool declares how."""
        mode = tool.get("verification_mode", "NONE")
        if mode == "NONE":
            return "NOT_APPLICABLE"
        if mode == "ECHO":
            return "VERIFIED" if result else "FAILED"
        if mode == "READ_BACK":
            if tool["tool_key"] == "tasks.create":
                task_id = result.get("task_id")
                if not task_id:
                    return "FAILED"
                exists = self._session.execute(
                    text("SELECT 1 FROM tasks WHERE tenant_id = :t AND id = CAST(:i AS uuid)"),
                    {"t": ctx.tenant_id, "i": task_id},
                ).first()
                return "VERIFIED" if exists else "FAILED"
            # A tool declaring READ_BACK without a verifier is a governance gap,
            # not something to quietly pass.
            return "PENDING"
        if mode == "RECEIPT":
            return "VERIFIED" if result.get("receipt_id") else "PENDING"
        return "NOT_APPLICABLE"

    def _record(
        self,
        ctx: ExecutionContext,
        result: ToolCallResult,
        parameters: dict[str, Any],
        idempotency_key: str,
        run_step_id: str | None,
    ) -> None:
        redacted_params = redact_payload(parameters)
        try:
            row = self._session.execute(
                text(
                    """
                    INSERT INTO tool_calls (tenant_id, run_id, run_step_id, tool_key, agent_key,
                                            user_id, correlation_id, idempotency_key,
                                            gateway_decision, denial_reason, denial_stage,
                                            parameters_hash, parameters_redacted, result_hash,
                                            result_redacted, verification_status, latency_ms)
                    VALUES (:t, :run, :step, :tool, :agent, :user, :corr, :idem, :decision,
                            :reason, :stage, :phash, CAST(:params AS jsonb), :rhash,
                            CAST(:res AS jsonb), :verif, :latency)
                    RETURNING id
                    """
                ),
                {
                    "t": ctx.tenant_id,
                    "run": ctx.run_id or None,
                    "step": run_step_id,
                    "tool": result.tool_key,
                    "agent": ctx.agent.agent_id if ctx.agent else "",
                    "user": ctx.human.user_id if ctx.human else None,
                    "corr": ctx.correlation_id,
                    "idem": idempotency_key,
                    "decision": result.decision,
                    "reason": result.denial_reason[:2000],
                    "stage": result.denial_stage,
                    "phash": content_hash(parameters),
                    "params": json.dumps(redacted_params, default=str),
                    "rhash": content_hash(result.result) if result.result else None,
                    "res": json.dumps(result.result, default=str) if result.result else None,
                    "verif": result.verification,
                    "latency": result.latency_ms,
                },
            ).one()
            result.call_id = str(row.id)
        except IntegrityError:
            # Concurrent call with the same idempotency key: the other writer won.
            self._session.rollback()
            return

        self._ledger.append(
            ctx,
            AuditEntry(
                category="TOOL_CALL",
                action=f"tool.{result.decision.lower()}",
                outcome="SUCCESS" if result.allowed else "DENIED",
                resource_type="tool",
                resource_id=result.tool_key,
                payload={
                    "decision": result.decision,
                    "denial_stage": result.denial_stage,
                    "denial_reason": result.denial_reason,
                    "parameters": redacted_params,
                    "risk_class": result.risk.get("risk_class"),
                    "policy_effect": result.policy.get("effect"),
                    "verification": result.verification,
                    "latency_ms": result.latency_ms,
                },
            ),
        )
