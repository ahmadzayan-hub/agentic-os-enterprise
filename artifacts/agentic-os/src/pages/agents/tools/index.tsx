import { useApi } from "@/lib/use-api";
import { Card, DataTable, Notice, Status, SurfaceError } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Tool {
  tool_key: string;
  name: string;
  description: string;
  kind: string;
  connector_key: string;
  side_effect: string;
  reversibility: string;
  risk_class: string;
  min_autonomy: string;
  requires_approval: boolean;
  verification_mode: string;
  implementation_status: string;
  status: string;
}

export default function ToolsPage() {
  const { data, error, status , loading } = useApi<{ tools: Tool[] }>("/api/v1/tools");
  if (loading) return <div className="empty">Loading...</div>;

  const notBuilt = data?.tools.filter((t) => t.implementation_status !== "IMPLEMENTED") ?? [];

  return (
    <div className="stack">
      <div>
        <h1>Tools</h1>
        <p className="page-lede">
          Every tool is reachable only through the Tool Security Gateway, which runs
          fourteen ordered checks before anything executes and names the stage of any
          refusal.
        </p>
      </div>

      {notBuilt.length > 0 ? (
        <Notice tone="warn">
          <strong>{notBuilt.length} declared capabilities are not implemented.</strong>{" "}
          They are registered so contracts, policies and approvals can name them, and
          the gateway refuses them with a 501. They never return fabricated data.
        </Notice>
      ) : null}

      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="the tool registry" />
      ) : (
        <Card>
          <DataTable
            caption="Tool registry"
            columns={[
              { key: "tool", label: "Tool" },
              { key: "implementation", label: "Implementation" },
              { key: "effect", label: "Side effect" },
              { key: "reversibility", label: "Reversibility" },
              { key: "autonomy", label: "Min autonomy" },
              { key: "approval", label: "Approval" },
              { key: "verification", label: "Verification" },
            ]}
            rows={data.tools.map((tool) => ({
              __key: tool.tool_key,
              tool: (
                <>
                  <strong className="mono">{tool.tool_key}</strong>
                  <div className="muted" style={{ fontSize: 12 }}>{tool.description}</div>
                </>
              ),
              implementation: (
                <Status
                  value={tool.implementation_status}
                  tone={tool.implementation_status === "IMPLEMENTED" ? "ok" : "warn"}
                />
              ),
              effect: <Status value={tool.side_effect} />,
              reversibility: (
                <Status
                  value={tool.reversibility}
                  tone={tool.reversibility === "REVERSIBLE" ? "ok" : "warn"}
                />
              ),
              autonomy: <span className="mono">{tool.min_autonomy}</span>,
              approval: tool.requires_approval ? (
                <span className="badge badge-warn">required</span>
              ) : (
                <span className="muted">—</span>
              ),
              verification: <span className="mono">{tool.verification_mode}</span>,
            }))}
          />
        </Card>
      )}
    </div>
  );
}
