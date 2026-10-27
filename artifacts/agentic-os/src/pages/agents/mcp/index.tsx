import { useApi } from "@/lib/use-api";
import { Card, DataTable, Empty, Notice, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface McpServer {
  server_key: string;
  name: string;
  provider: string;
  endpoint: string;
  transport: string;
  trust_class: string;
  authorization_method: string;
  data_classification: string;
  allowed_agents: string[];
  forward_user_token: boolean;
  status: string;
  last_security_review: string | null;
  last_used_at: string | null;
  tool_count: number;
  approved_tool_count: number;
}

export default function McpPage() {
  const { data, error, status , loading } = useApi<{ servers: McpServer[] }>("/api/v1/mcp");
  if (loading) return <div className="empty">Loading...</div>;


  return (
    <div className="stack">
      <div>
        <h1>MCP registry</h1>
        <p className="page-lede">
          Agents never connect to an MCP server directly. The gateway checks trust
          classification, agent and role allowlists, and the approved schema hash of
          the specific tool before any call leaves the platform.
        </p>
      </div>

      <Notice tone="info">
        A server that changes an approved tool&rsquo;s schema has that
        tool&rsquo;s approval revoked automatically and a security finding raised.
        Token forwarding is only ever permitted to internally operated servers, which
        a database constraint enforces as well as the code.
      </Notice>

      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="the MCP registry" />
      ) : data.servers.length === 0 ? (
        <Card>
          <Empty>
            No MCP server is registered. Newly registered servers default to
            EXPERIMENTAL, which the gateway refuses to invoke until a human
            classifies them after a security review.
          </Empty>
        </Card>
      ) : (
        <Card>
          <DataTable
            caption="Registered MCP servers"
            columns={[
              { key: "server", label: "Server" },
              { key: "trust", label: "Trust class" },
              { key: "auth", label: "Authorization" },
              { key: "tools", label: "Tools", numeric: true },
              { key: "forwarding", label: "Token forwarding" },
              { key: "review", label: "Last review" },
            ]}
            rows={data.servers.map((server) => ({
              __key: server.server_key,
              server: (
                <>
                  <strong className="mono">{server.server_key}</strong>
                  <div className="muted" style={{ fontSize: 12 }}>{server.endpoint}</div>
                </>
              ),
              trust: (
                <Status
                  value={server.trust_class}
                  tone={
                    server.trust_class === "TRUSTED_INTERNAL" ||
                    server.trust_class === "APPROVED_EXTERNAL"
                      ? "ok"
                      : server.trust_class === "QUARANTINED"
                        ? "danger"
                        : "warn"
                  }
                />
              ),
              auth: <span className="mono">{server.authorization_method}</span>,
              tools: `${server.approved_tool_count} / ${server.tool_count}`,
              forwarding: server.forward_user_token ? (
                <span className="badge badge-warn">enabled</span>
              ) : (
                <span className="badge badge-ok">disabled</span>
              ),
              review: <span className="mono">{formatWhen(server.last_security_review)}</span>,
            }))}
          />
        </Card>
      )}
    </div>
  );
}
