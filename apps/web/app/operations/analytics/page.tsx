import { Card, DataTable, Stat, SurfaceError, formatDuration } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Metrics {
  window_hours: number;
  runs: {
    total: number;
    succeeded: number;
    failed: number;
    success_rate: number | null;
    p95_duration_ms: number;
  };
  tools: {
    total: number;
    denied: number;
    denial_rate: number | null;
    verification_failed: number;
    avg_latency_ms: number;
  };
  retrieval: { queries: number; avg_latency_ms: number; chunks_withheld_by_acl: number };
  security: { findings: number; severe_findings: number };
  policy: { decisions: number; denied: number; escalated_to_approval: number };
  otel_endpoint_configured: boolean;
}

export default async function AnalyticsPage() {
  const { data, error, status } = await apiTry<Metrics>("/api/v1/analytics?window_hours=168");

  if (!data) {
    return (
      <>
        <h1>Analytics</h1>
        <SurfaceError error={error ?? ""} status={status} what="analytics" />
      </>
    );
  }

  return (
    <div className="stack">
      <div>
        <h1>Analytics</h1>
        <p className="page-lede">
          Computed from the platform&rsquo;s own recorded activity over the last{" "}
          {data.window_hours} hours. Nothing here is projected or assumed.
        </p>
      </div>

      <div className="grid grid-4">
        <Stat label="Runs" value={data.runs.total} note={`${data.runs.failed} failed`} />
        <Stat
          label="Success rate"
          value={data.runs.success_rate === null ? "—" : `${(data.runs.success_rate * 100).toFixed(1)}%`}
        />
        <Stat label="p95 run duration" value={formatDuration(data.runs.p95_duration_ms)} />
        <Stat
          label="Tool denial rate"
          value={data.tools.denial_rate === null ? "—" : `${(data.tools.denial_rate * 100).toFixed(1)}%`}
          note={`${data.tools.denied} of ${data.tools.total}`}
        />
      </div>

      <div className="grid grid-2">
        <Card title="Retrieval">
          <DataTable
            caption="Retrieval metrics"
            columns={[
              { key: "metric", label: "Metric" },
              { key: "value", label: "Value", numeric: true },
            ]}
            rows={[
              { __key: "q", metric: "Queries", value: data.retrieval.queries },
              {
                __key: "l",
                metric: "Average latency",
                value: formatDuration(data.retrieval.avg_latency_ms),
              },
              {
                __key: "a",
                metric: "Chunks withheld by access control",
                value: data.retrieval.chunks_withheld_by_acl,
              },
            ]}
          />
        </Card>

        <Card title="Governance activity">
          <DataTable
            caption="Policy and security metrics"
            columns={[
              { key: "metric", label: "Metric" },
              { key: "value", label: "Value", numeric: true },
            ]}
            rows={[
              { __key: "d", metric: "Policy decisions", value: data.policy.decisions },
              { __key: "dn", metric: "Denied by policy", value: data.policy.denied },
              {
                __key: "e",
                metric: "Escalated to human approval",
                value: data.policy.escalated_to_approval,
              },
              { __key: "s", metric: "Security findings", value: data.security.findings },
              {
                __key: "sv",
                metric: "High or critical findings",
                value: data.security.severe_findings,
              },
              {
                __key: "vf",
                metric: "Side-effect verifications failed",
                value: data.tools.verification_failed,
              },
            ]}
          />
        </Card>
      </div>

      <Card title="Telemetry export">
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          OpenTelemetry export is{" "}
          {data.otel_endpoint_configured ? (
            <span className="badge badge-ok">configured</span>
          ) : (
            <span className="badge badge-muted">not configured</span>
          )}
          . Spans and metrics are persisted in the platform tables regardless, so run
          traces remain queryable without an external backend.
        </p>
      </Card>
    </div>
  );
}
