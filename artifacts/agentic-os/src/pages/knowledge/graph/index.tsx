import { useState, useEffect } from "react";
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

export default function GraphPage() {
  const searchParams = new URLSearchParams(window.location.search);
  const node = searchParams.get("node") || "";
  const type = searchParams.get("type") || "";

  const query = new URLSearchParams();
  if (node) query.set("node_key", node);
  if (type) query.set("node_type", type);
  query.set("limit", "100");

  const [data, setData] = useState<{
    nodes: GraphNode[];
    edges: { from: string; to: string; relation: string; confidence: number }[];
    root?: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number>(0);
  const [loading, setLoading] = useState(true);

  const [impactData, setImpactData] = useState<{
    affected: { node_key: string; node_type: string; label: string; distance: number }[];
    affected_count: number;
    note: string;
  } | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    
    apiTry<any>(`/api/v1/graph?${query.toString()}`).then((res) => {
      if (!mounted) return;
      setData(res.data);
      setError(res.error);
      setStatus(res.status);
      setLoading(false);
    });
    
    if (node) {
      apiTry<any>(`/api/v1/graph/impact?node_key=${encodeURIComponent(node)}&depth=3`).then((res) => {
        if (!mounted) return;
        setImpactData(res.data);
      });
    }

    return () => { mounted = false; };
  }, [node, type]);

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const n = form.get("node") as string;
    const t = form.get("type") as string;
    const search = new URLSearchParams();
    if (n) search.set("node", n);
    if (t) search.set("type", t);
    window.location.href = `/knowledge/graph?${search.toString()}`;
  };

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
        <form onSubmit={handleSubmit} className="row">
          <label htmlFor="node" className="visually-hidden">
            Node key
          </label>
          <input
            id="node"
            name="node"
            type="text"
            placeholder="AST-4012"
            defaultValue={node}
            style={{ maxWidth: 260 }}
          />
          <label htmlFor="type" className="visually-hidden">
            Node type
          </label>
          <select id="type" name="type" defaultValue={type} style={{ maxWidth: 200 }}>
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

      {loading ? (
        <div className="empty">Loading...</div>
      ) : !data ? (
        <SurfaceError error={error ?? ""} status={status} what="the graph" />
      ) : (
        <Card title={node ? `Neighbourhood of ${node}` : "Nodes"}>
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

      {impactData ? (
        <Card title={`Impact analysis · ${impactData.affected_count} affected`}>
          {impactData.affected.length === 0 ? (
            <Empty>Nothing downstream of this node has been asserted.</Empty>
          ) : (
            <DataTable
              caption="Downstream impact"
              columns={[
                { key: "node", label: "Affected node" },
                { key: "type", label: "Type" },
                { key: "distance", label: "Hops", numeric: true },
              ]}
              rows={impactData.affected.map((n) => ({
                __key: n.node_key,
                node: n.label,
                type: <span className="mono">{n.node_type}</span>,
                distance: n.distance,
              }))}
            />
          )}
          <div style={{ marginTop: 12 }}>
            <Notice tone="warn">{impactData.note}</Notice>
          </div>
        </Card>
      ) : null}
    </div>
  );
}