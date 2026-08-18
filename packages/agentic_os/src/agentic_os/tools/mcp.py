"""MCP Registry and Gateway.

Agents never connect to an MCP server. They ask the gateway, which:

* resolves the server from the registry and refuses anything not TRUSTED_INTERNAL
  or APPROVED_EXTERNAL;
* checks the agent and the caller's role against the server's allowlists;
* refuses to forward the caller's identity token to anything but an internally
  operated server (enforced by a database CHECK constraint as well);
* mints a scoped, short-lived credential per call from the secret broker;
* validates the tool's discovered schema against the approved hash, so a server
  cannot silently change a tool's contract after approval — the "rug pull"
  failure mode;
* screens every response through the context firewall before it can reach a
  model context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.ai.context_firewall import TrustTier, screen
from agentic_os.assurance.audit import AuditEntry, AuditLedger
from agentic_os.core.config import get_settings
from agentic_os.core.context import ExecutionContext
from agentic_os.core.crypto import content_hash
from agentic_os.core.errors import (
    AuthorizationError,
    Conflict,
    NotFound,
    PolicyDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
    ValidationError,
)
from agentic_os.core.ids import utcnow

#: Trust classes permitted to serve a call at all.
INVOCABLE_TRUST = frozenset({"TRUSTED_INTERNAL", "APPROVED_EXTERNAL"})

#: How stale a security review may be before the server is refused.
SECURITY_REVIEW_MAX_AGE_DAYS = 365


@dataclass(slots=True)
class McpServerRecord:
    server_key: str
    name: str
    endpoint: str
    transport: str
    trust_class: str
    authorization_method: str
    data_classification: str
    allowed_agents: list[str] = field(default_factory=list)
    allowed_roles: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    forward_user_token: bool = False
    status: str = "ACTIVE"
    last_security_review: Any = None

    @property
    def invocable(self) -> bool:
        return self.trust_class in INVOCABLE_TRUST and self.status == "ACTIVE"


def register_server(
    session: Session,
    ctx: ExecutionContext,
    *,
    server_key: str,
    name: str,
    endpoint: str,
    provider: str = "",
    owner_team: str = "",
    transport: str = "http",
    trust_class: str = "EXPERIMENTAL",
    authorization_method: str = "NONE",
    data_classification: str = "INTERNAL",
    network_destinations: list[str] | None = None,
    allowed_agents: list[str] | None = None,
    allowed_roles: list[str] | None = None,
    scopes: list[str] | None = None,
    forward_user_token: bool = False,
) -> str:
    """Register an MCP server. New servers default to EXPERIMENTAL (unusable)."""
    if forward_user_token and trust_class != "TRUSTED_INTERNAL":
        raise PolicyDenied(
            "user token forwarding is only permitted to internally operated MCP servers",
            details={"trust_class": trust_class},
        )
    row = session.execute(
        text(
            """
            INSERT INTO mcp_servers (tenant_id, server_key, name, provider, owner_team, endpoint,
                                     transport, trust_class, authorization_method,
                                     data_classification, network_destinations, allowed_agents,
                                     allowed_roles, scopes, forward_user_token)
            VALUES (:t, :k, :n, :p, :o, :e, :tr, :tc, :am,
                    CAST(:dc AS data_classification), :nd, :aa, :ar, :sc, :fut)
            ON CONFLICT (tenant_id, server_key) DO UPDATE SET
              name = EXCLUDED.name, endpoint = EXCLUDED.endpoint,
              transport = EXCLUDED.transport, authorization_method = EXCLUDED.authorization_method,
              data_classification = EXCLUDED.data_classification,
              network_destinations = EXCLUDED.network_destinations,
              allowed_agents = EXCLUDED.allowed_agents, allowed_roles = EXCLUDED.allowed_roles,
              scopes = EXCLUDED.scopes
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id, "k": server_key, "n": name, "p": provider, "o": owner_team,
            "e": endpoint, "tr": transport, "tc": trust_class, "am": authorization_method,
            "dc": data_classification, "nd": network_destinations or [],
            "aa": allowed_agents or [], "ar": allowed_roles or [], "sc": scopes or [],
            "fut": forward_user_token,
        },
    ).one()
    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="CONFIG_CHANGE",
            action="mcp.server_registered",
            resource_type="mcp_server",
            resource_id=server_key,
            payload={"trust_class": trust_class, "endpoint": endpoint, "transport": transport},
        ),
    )
    return str(row.id)


def classify_server(
    session: Session, ctx: ExecutionContext, server_key: str, trust_class: str, *, reason: str
) -> None:
    """Change a server's trust classification. Always audited."""
    valid = {"TRUSTED_INTERNAL", "APPROVED_EXTERNAL", "EXPERIMENTAL", "DISABLED", "QUARANTINED"}
    if trust_class not in valid:
        raise ValidationError(f"unknown trust class '{trust_class}'")
    result = session.execute(
        text(
            "UPDATE mcp_servers SET trust_class = :tc, last_security_review = now() "
            "WHERE tenant_id = :t AND server_key = :k"
        ),
        {"tc": trust_class, "t": ctx.tenant_id, "k": server_key},
    )
    if result.rowcount == 0:
        raise NotFound(f"MCP server '{server_key}' is not registered")
    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="SECURITY",
            action="mcp.trust_reclassified",
            resource_type="mcp_server",
            resource_id=server_key,
            payload={"trust_class": trust_class, "reason": reason},
        ),
    )


def get_server(session: Session, ctx: ExecutionContext, server_key: str) -> McpServerRecord:
    row = session.execute(
        text("SELECT * FROM mcp_servers WHERE tenant_id = :t AND server_key = :k"),
        {"t": ctx.tenant_id, "k": server_key},
    ).mappings().first()
    if row is None:
        raise NotFound(f"MCP server '{server_key}' is not registered")
    return McpServerRecord(
        server_key=row["server_key"],
        name=row["name"],
        endpoint=row["endpoint"],
        transport=row["transport"],
        trust_class=row["trust_class"],
        authorization_method=row["authorization_method"],
        data_classification=str(row["data_classification"]),
        allowed_agents=list(row["allowed_agents"] or []),
        allowed_roles=list(row["allowed_roles"] or []),
        scopes=list(row["scopes"] or []),
        forward_user_token=bool(row["forward_user_token"]),
        status=str(row["status"]),
        last_security_review=row["last_security_review"],
    )


def list_servers(session: Session, ctx: ExecutionContext) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT server_key, name, provider, owner_team, endpoint, transport, trust_class,
                   authorization_method, data_classification, allowed_agents, allowed_roles,
                   scopes, forward_user_token, status, last_security_review, last_used_at,
                   created_at,
                   (SELECT count(*) FROM mcp_tools mt WHERE mt.mcp_server_id = s.id) AS tool_count,
                   (SELECT count(*) FROM mcp_tools mt
                     WHERE mt.mcp_server_id = s.id AND mt.approved) AS approved_tool_count
            FROM mcp_servers s
            WHERE tenant_id = :t
            ORDER BY server_key
            """
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return [dict(r) for r in rows]


def record_discovered_tools(
    session: Session, ctx: ExecutionContext, server_key: str, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    """Record tools advertised by a server and flag any schema that changed.

    A server that alters an approved tool's schema has its approval for that
    tool revoked automatically. This is the defence against a server that
    behaves during review and changes afterwards.
    """
    server = session.execute(
        text("SELECT id FROM mcp_servers WHERE tenant_id = :t AND server_key = :k"),
        {"t": ctx.tenant_id, "k": server_key},
    ).first()
    if server is None:
        raise NotFound(f"MCP server '{server_key}' is not registered")

    changed: list[str] = []
    added: list[str] = []
    for tool in tools:
        name = str(tool["name"])
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        schema_hash = content_hash(schema)
        existing = session.execute(
            text(
                "SELECT schema_hash, approved FROM mcp_tools "
                "WHERE mcp_server_id = :s AND tool_name = :n"
            ),
            {"s": server.id, "n": name},
        ).mappings().first()

        if existing is None:
            added.append(name)
        elif existing["schema_hash"] and existing["schema_hash"] != schema_hash:
            changed.append(name)

        session.execute(
            text(
                """
                INSERT INTO mcp_tools (tenant_id, mcp_server_id, tool_name, description,
                                       input_schema, schema_hash)
                VALUES (:t, :s, :n, :d, CAST(:sc AS jsonb), :h)
                ON CONFLICT (mcp_server_id, tool_name) DO UPDATE SET
                  description = EXCLUDED.description,
                  input_schema = EXCLUDED.input_schema,
                  schema_hash = EXCLUDED.schema_hash,
                  approved = CASE
                    WHEN mcp_tools.schema_hash IS DISTINCT FROM EXCLUDED.schema_hash
                    THEN false ELSE mcp_tools.approved END,
                  discovered_at = now()
                """
            ),
            {
                "t": ctx.tenant_id, "s": server.id, "n": name,
                "d": str(tool.get("description", ""))[:2000],
                "sc": json.dumps(schema, default=str), "h": schema_hash,
            },
        )

    if changed:
        AuditLedger(session).append(
            ctx,
            AuditEntry(
                category="SECURITY",
                action="mcp.tool_schema_changed",
                outcome="DENIED",
                resource_type="mcp_server",
                resource_id=server_key,
                payload={"tools": changed, "effect": "approval revoked pending re-review"},
            ),
        )
        session.execute(
            text(
                """
                INSERT INTO security_findings (tenant_id, finding_type, severity, source, detail)
                VALUES (:t, 'MCP_TOOL_SCHEMA_CHANGED', 'HIGH', :src, CAST(:d AS jsonb))
                """
            ),
            {
                "t": ctx.tenant_id,
                "src": f"mcp:{server_key}",
                "d": json.dumps({"tools": changed}),
            },
        )

    return {"discovered": len(tools), "added": added, "schema_changed": changed}


def approve_tool(
    session: Session, ctx: ExecutionContext, server_key: str, tool_name: str
) -> None:
    if ctx.human is None:
        raise AuthorizationError("MCP tool approval requires a human principal")
    result = session.execute(
        text(
            """
            UPDATE mcp_tools SET approved = true, approved_by = CAST(:u AS uuid),
                                 approved_at = now()
            WHERE tenant_id = :t AND tool_name = :n
              AND mcp_server_id = (SELECT id FROM mcp_servers
                                    WHERE tenant_id = :t AND server_key = :k)
            """
        ),
        {"t": ctx.tenant_id, "n": tool_name, "k": server_key, "u": ctx.human.user_id},
    )
    if result.rowcount == 0:
        raise NotFound(f"MCP tool '{tool_name}' on server '{server_key}' not found")
    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="CONFIG_CHANGE",
            action="mcp.tool_approved",
            resource_type="mcp_tool",
            resource_id=f"{server_key}:{tool_name}",
            payload={"server": server_key, "tool": tool_name},
        ),
    )


class McpGateway:
    """The only path from an agent to an MCP server."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._ledger = AuditLedger(session)

    def authorize_call(
        self, ctx: ExecutionContext, server_key: str, tool_name: str
    ) -> McpServerRecord:
        """Run every admission check. Raises on the first failure."""
        server = get_server(self._session, ctx, server_key)

        if not server.invocable:
            raise PolicyDenied(
                f"MCP server '{server_key}' is {server.trust_class}/{server.status} and "
                "may not be invoked",
                details={"trust_class": server.trust_class, "status": server.status},
            )

        if server.last_security_review is None:
            raise PolicyDenied(
                f"MCP server '{server_key}' has never had a security review",
                details={"trust_class": server.trust_class},
            )
        age_days = (utcnow() - server.last_security_review).days
        if age_days > SECURITY_REVIEW_MAX_AGE_DAYS:
            raise PolicyDenied(
                f"MCP server '{server_key}' security review is {age_days} days old",
                details={"max_age_days": SECURITY_REVIEW_MAX_AGE_DAYS},
            )

        if server.allowed_agents:
            agent_key = ctx.agent.agent_id if ctx.agent else ""
            if agent_key not in server.allowed_agents:
                raise AuthorizationError(
                    f"agent '{agent_key or '(none)'}' is not on the allowlist for MCP server "
                    f"'{server_key}'"
                )
        if server.allowed_roles:
            roles = ctx.human.roles if ctx.human else frozenset()
            if not (set(server.allowed_roles) & set(roles)):
                raise AuthorizationError(
                    f"principal holds no role permitted on MCP server '{server_key}'"
                )

        approved = self._session.execute(
            text(
                """
                SELECT mt.approved, mt.input_schema, mt.schema_hash
                FROM mcp_tools mt
                JOIN mcp_servers s ON s.id = mt.mcp_server_id
                WHERE s.tenant_id = :t AND s.server_key = :k AND mt.tool_name = :n
                """
            ),
            {"t": ctx.tenant_id, "k": server_key, "n": tool_name},
        ).mappings().first()
        if approved is None:
            raise NotFound(
                f"MCP tool '{tool_name}' has not been discovered on server '{server_key}'"
            )
        if not approved["approved"]:
            raise PolicyDenied(
                f"MCP tool '{tool_name}' on '{server_key}' is not approved for use",
                details={"remediation": "a human must approve the tool's current schema"},
            )
        return server

    def call(
        self,
        ctx: ExecutionContext,
        server_key: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Invoke an approved MCP tool and screen the response."""
        server = self.authorize_call(ctx, server_key, tool_name)

        if server.transport != "http":
            raise UpstreamUnavailable(
                f"MCP transport '{server.transport}' is declared but not implemented; "
                "only http is wired",
                details={"server": server_key, "transport": server.transport},
            )

        settings = get_settings()
        self._check_egress(server.endpoint)

        headers = {"content-type": "application/json"}
        if server.authorization_method == "API_KEY":
            from agentic_os.tools.secrets import SecretBroker

            value, handle = SecretBroker(self._session).resolve(
                f"mcp.{server_key}.api_key", tenant_id=ctx.tenant_id
            )
            headers["authorization"] = f"Bearer {value}"
            credential_fingerprint = handle.fingerprint[:16]
        else:
            credential_fingerprint = ""

        body = {
            "jsonrpc": "2.0",
            "id": ctx.correlation_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        try:
            with httpx.Client(
                timeout=timeout_seconds or settings.model_request_timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = client.post(server.endpoint, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout(f"MCP server '{server_key}' timed out") from exc
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable(f"MCP server '{server_key}' unreachable: {exc}") from exc

        if response.status_code >= 400:
            raise UpstreamUnavailable(
                f"MCP server '{server_key}' returned {response.status_code}",
                details={"status": response.status_code},
            )

        payload = response.json()
        raw_text = json.dumps(payload.get("result", payload), default=str)

        # MCP responses are TOOL_GENERATED: the least trusted tier there is.
        screened = screen(
            raw_text,
            TrustTier.TOOL_GENERATED,
            source_ref=f"mcp:{server_key}:{tool_name}",
        )
        if screened.injection_detected:
            self._session.execute(
                text(
                    """
                    INSERT INTO security_findings (tenant_id, finding_type, severity, source,
                                                   run_id, detail)
                    VALUES (:t, 'MCP_RESPONSE_INJECTION', :sev, :src, :run, CAST(:d AS jsonb))
                    """
                ),
                {
                    "t": ctx.tenant_id,
                    "sev": "CRITICAL" if screened.blocked else "HIGH",
                    "src": f"mcp:{server_key}:{tool_name}",
                    "run": ctx.run_id or None,
                    "d": json.dumps(screened.to_dict(), default=str),
                },
            )

        self._session.execute(
            text("UPDATE mcp_servers SET last_used_at = now() WHERE tenant_id = :t AND server_key = :k"),
            {"t": ctx.tenant_id, "k": server_key},
        )
        self._ledger.append(
            ctx,
            AuditEntry(
                category="TOOL_CALL",
                action="mcp.tool_called",
                outcome="DENIED" if screened.blocked else "SUCCESS",
                resource_type="mcp_tool",
                resource_id=f"{server_key}:{tool_name}",
                payload={
                    "trust_class": server.trust_class,
                    "credential_fingerprint": credential_fingerprint,
                    "injection_detected": screened.injection_detected,
                    "blocked": screened.blocked,
                    "status_code": response.status_code,
                },
            ),
        )

        if screened.blocked:
            raise PolicyDenied(
                f"the response from MCP tool '{tool_name}' was withheld: it contained "
                "prompt-injection indicators",
                details=screened.to_dict(),
            )

        return {
            "server_key": server_key,
            "tool_name": tool_name,
            "content": screened.text,
            "trust_tier": screened.tier.name,
            "injection_detected": screened.injection_detected,
            "provenance": screened.to_dict(),
        }

    def _check_egress(self, endpoint: str) -> None:
        """Refuse endpoints outside the configured egress allowlist."""
        from urllib.parse import urlparse

        settings = get_settings()
        parsed = urlparse(endpoint)
        host = parsed.hostname or ""
        if not host:
            raise ValidationError(f"MCP endpoint '{endpoint}' has no host")

        if settings.egress_block_private_networks:
            import ipaddress

            try:
                address = ipaddress.ip_address(host)
                if address.is_private or address.is_loopback or address.is_link_local:
                    if not _is_permitted_local(host, settings.egress_allowlist):
                        raise PolicyDenied(
                            f"egress to private address '{host}' is blocked (SSRF protection)",
                            details={"endpoint": endpoint},
                        )
            except ValueError:
                if host in ("localhost", "metadata.google.internal") and not _is_permitted_local(
                    host, settings.egress_allowlist
                ):
                    raise PolicyDenied(
                        f"egress to '{host}' is blocked (SSRF protection)",
                        details={"endpoint": endpoint},
                    ) from None

        if settings.egress_allowlist and host not in settings.egress_allowlist:
            raise PolicyDenied(
                f"host '{host}' is not on the egress allowlist",
                details={"allowlist": list(settings.egress_allowlist)},
            )


def _is_permitted_local(host: str, allowlist: tuple[str, ...]) -> bool:
    return host in allowlist


def revoke_server(session: Session, ctx: ExecutionContext, server_key: str, reason: str) -> None:
    """Quarantine a server and revoke every tool approval it holds."""
    classify_server(session, ctx, server_key, "QUARANTINED", reason=reason)
    session.execute(
        text(
            """
            UPDATE mcp_tools SET approved = false
            WHERE mcp_server_id = (SELECT id FROM mcp_servers
                                    WHERE tenant_id = :t AND server_key = :k)
            """
        ),
        {"t": ctx.tenant_id, "k": server_key},
    )
