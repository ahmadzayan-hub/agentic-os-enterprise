import { Card, DataTable, Empty, Notice, SurfaceError } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface GraphNode {
  node_key: string;
  node_type: string;
  label: string;
  classification: string;
  confidence: number;
  source_ref: string;
}

export default async function GraphPage({
  searchParams,
}: {
  searchParams: Promise<{ node?: string; type?: string }>;
}) {
  const params = await searchParams;
  const query = new URLSearchParams();
  if (params.node) query.set("node_key", params.node);
  if (params.type) query.set("node_type", params.type);
  query.set("limit", "100");

  const { data, error, status } = await apiTry<{
    nodes: GraphNode[];
    edges: { from: string; to: string; relation: string; confidence: number }[];
    root?: string;
  }>(`/api/v1/graph?${query.toString()}`);

  const impact = params.node
    ? await apiTry<{
        affected: { node_key: string; node_type: string; label: string; distance: number }[];
        affected_count: number;
        note: string;
      }>(`/api/v1/graph/impact?node_key=${encodeURIComponent(params.node)}&depth=3`)
    : null;

  return (
    <div className="stack">
      <div>
        <h1>G-Brain</h1>
        <p className="page-lede">
          The enterprise intelligence graph. Every node and edge carries a source
          reference and a confidence, so a dependency can be traced back to the
          document that asserted it.
        </p>
      </div>

      <Card title="Find a node">
        <form method="get" className="row">
          <label htmlFor="node" className="visually-hidden">
            Node key
          </label>
          <input
            id="node"
            name="node"
            type="text"
            placeholder="AST-4012"
            defaultValue={params.node ?? ""}
            style={{ maxWidth: 260 }}
          />
          <label htmlFor="type" className="visually-hidden">
            Node type
          </label>
          <select id="type" name="type" defaultValue={params.type ?? ""} style={{ maxWidth: 200 }}>
            <option value="">All types</option>
            {["ASSET", "PROCESS", "DOCUMENT", "PROJECT", "CONTRACT", "RISK", "APPLICATION"].map(
              (option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ),
            )}
          </select>
          <button className="btn btn-primary" type="submit">
            Query
          </button>
        </form>
      </Card>

      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="the graph" />
      ) : (
        <Card title={params.node ? `Neighbourhood of ${params.node}` : "Nodes"}>
          <DataTable
            caption="Graph nodes"
            empty="No node matches."
            columns={[
              { key: "node", label: "Node" },
              { key: "type", label: "Type" },
              { key: "classification", label: "Classification" },
              { key: "source", label: "Source" },
            ]}
            rows={data.nodes.map((node) => ({
              __key: node.node_key,
              node: (
                <a href={`/knowledge/graph?node=${encodeURIComponent(node.node_key)}`}>
                  {node.label}
                </a>
              ),
              type: <span className="mono">{node.node_type}</span>,
              classification: <span className="mono muted">{node.classification}</span>,
              source: <span className="mono muted">{node.source_ref || "—"}</span>,
            }))}
          />
        </Card>
      )}

      {impact?.data ? (
        <Card title={`Impact analysis · ${impact.data.affected_count} affected`}>
          {impact.data.affected.length === 0 ? (
            <Empty>Nothing downstream of this node has been asserted.</Empty>
          ) : (
            <DataTable
              caption="Downstream impact"
              columns={[
                { key: "node", label: "Affected node" },
                { key: "type", label: "Type" },
                { key: "distance", label: "Hops", numeric: true },
              ]}
              rows={impact.data.affected.map((node) => ({
                __key: node.node_key,
                node: node.label,
                type: <span className="mono">{node.node_type}</span>,
                distance: node.distance,
              }))}
            />
          )}
          <div style={{ marginTop: 12 }}>
            <Notice tone="warn">{impact.data.note}</Notice>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
