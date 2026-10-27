import { useApi } from "@/lib/use-api";
import { Link } from 'wouter';

import { AskForm } from "@/components/ask-form";
import {
  Card,
  DataTable,
  Empty,
  Notice,
  Stat,
  Status,
  SurfaceError,
  formatCost,
  formatDuration,
  formatWhen,
} from "@/components/ui";
import { apiTry } from "@/lib/api";

interface CommandCenter {
  data_provenance: {
    mode: "SAMPLE" | "PERSISTED";
    label: string;
    derived_from_persisted_evidence: boolean;
    generated_at: string;
  };
  requires_attention: {
    pending_approvals: {
      id: string;
      action: string;
      target: string;
      risk_class: string;
      financial_impact_usd: number;
      expires_at: string;
    }[];
    failed_runs: {
      id: string;
      objective: string;
      error_class: string | null;
      completed_at: string | null;
    }[];
    security_findings: {
      finding_type: string;
      severity: string;
      source: string;
      created_at: string;
    }[];
    open_incidents: {
      incident_key: string;
      title: string;
      severity: string;
      status: string;
    }[];
    dead_letters: number;
    expired_evidence: number;
  };
  agent_operations: {
    runs: {
      total: number;
      succeeded: number;
      failed: number;
      success_rate: number | null;
      p95_duration_ms: number;
    };
    tools: { total: number; denied: number; denial_rate: number | null };
    retrieval: { queries: number; chunks_withheld_by_acl: number };
    security: { findings: number; severe_findings: number };
    policy: { decisions: number; denied: number; escalated_to_approval: number };
  };
  business_pulse: {
    runs_total: number;
    runs_succeeded: number;
    runs_awaiting_approval: number;
    success_rate: number | null;
    cost_usd: number;
  };
  engaged_kill_switches: { scope: string; target_key: string; reason: string }[];
  read_only_mode: boolean;
}

export default function CommandCenterPage() {
  const { data, error, status , loading } = useApi<CommandCenter>("/api/v1/command-center");
  if (loading) return <div className="empty">Loading...</div>;


  if (!data) {
    return (
      <>
        <h1>Command Center</h1>
        <SurfaceError error={error ?? ""} status={status} what="the command centre" />
      </>
    );
  }

  const attention = data.requires_attention;
  const ops = data.agent_operations;
  const attentionCount =
    attention.pending_approvals.length +
    attention.failed_runs.length +
    attention.security_findings.length +
    attention.open_incidents.length +
    (attention.dead_letters > 0 ? 1 : 0) +
    (attention.expired_evidence > 0 ? 1 : 0);

  return (
    <div className="stack">
      <div>
        <h1>Command Center</h1>
        <p className="page-lede">
          Organised around what needs a decision, not around what happens to be
          countable. Figures marked as sample illustrate the operating model and are
          not derived from persisted evidence.
        </p>
      </div>

      {!data.data_provenance.derived_from_persisted_evidence ? (
        <Notice tone="warn">
          <strong>{data.data_provenance.label}.</strong> These figures are illustrative,
          not production evidence.
        </Notice>
      ) : null}

      {data.engaged_kill_switches.length > 0 ? (
        <Notice tone="danger">
          <strong>Kill switch engaged.</strong>{" "}
          {data.engaged_kill_switches
            .map((k) => `${k.scope}${k.target_key ? `:${k.target_key}` : ""}`)
            .join(", ")}
          . Agent execution is halted in this scope.
        </Notice>
      ) : null}

      <AskForm />

      {/* ------------------------------------------------ requires attention */}
      <section aria-labelledby="attention-heading" className="stack">
        <h2 id="attention-heading">
          Requires attention{" "}
          <span className={`badge badge-${attentionCount ? "warn" : "ok"}`}>
            {attentionCount} item{attentionCount === 1 ? "" : "s"}
          </span>
        </h2>

        <div className="grid grid-2">
          <Card
            title="Approvals waiting on you"
            action={<Link href="/approvals">All approvals</Link>}
          >
            {attention.pending_approvals.length === 0 ? (
              <Empty>Nothing is waiting for your authorisation.</Empty>
            ) : (
              <DataTable
                caption="Approvals awaiting your decision"
                columns={[
                  { key: "action", label: "Action" },
                  { key: "risk", label: "Risk" },
                  { key: "impact", label: "Impact", numeric: true },
                  { key: "expires", label: "Expires" },
                ]}
                rows={attention.pending_approvals.map((approval) => ({
                  __key: approval.id,
                  action: (
                    <Link href={`/approvals#${approval.id}`}>{approval.action}</Link>
                  ),
                  risk: <Status value={approval.risk_class} />,
                  impact: formatCost(approval.financial_impact_usd),
                  expires: <span className="mono">{formatWhen(approval.expires_at)}</span>,
                }))}
              />
            )}
          </Card>

          <Card title="Failed runs" action={<Link href="/runs?status=FAILED">All runs</Link>}>
            {attention.failed_runs.length === 0 ? (
              <Empty>No failed runs.</Empty>
            ) : (
              <DataTable
                caption="Recently failed runs"
                columns={[
                  { key: "objective", label: "Objective" },
                  { key: "error", label: "Error" },
                  { key: "when", label: "When" },
                ]}
                rows={attention.failed_runs.map((run) => ({
                  __key: run.id,
                  objective: <Link href={`/runs/${run.id}`}>{run.objective.slice(0, 60)}</Link>,
                  error: <Status value={run.error_class ?? undefined} tone="danger" />,
                  when: <span className="mono">{formatWhen(run.completed_at)}</span>,
                }))}
              />
            )}
          </Card>

          <Card title="Security findings" action={<Link href="/security">Security</Link>}>
            {attention.security_findings.length === 0 ? (
              <Empty>No high or critical findings recorded.</Empty>
            ) : (
              <DataTable
                caption="High and critical security findings"
                columns={[
                  { key: "type", label: "Finding" },
                  { key: "severity", label: "Severity" },
                  { key: "source", label: "Source" },
                ]}
                rows={attention.security_findings.map((finding, index) => ({
                  __key: index,
                  type: finding.finding_type,
                  severity: <Status value={finding.severity} />,
                  source: <span className="mono">{finding.source}</span>,
                }))}
              />
            )}
          </Card>

          <Card title="Assurance and reliability">
            <div className="grid grid-2">
              <div>
                <div className="stat-label">Expired evidence</div>
                <div className="stat-value">{attention.expired_evidence}</div>
                <div className="stat-note">
                  <Link href="/governance/evidence">Evidence</Link>
                </div>
              </div>
              <div>
                <div className="stat-label">Dead-letter events</div>
                <div className="stat-value">{attention.dead_letters}</div>
                <div className="stat-note">undeliverable after retry</div>
              </div>
            </div>
            {attention.open_incidents.length > 0 ? (
              <div style={{ marginTop: 14 }}>
                <h3>Open incidents</h3>
                <DataTable
                  caption="Open incidents"
                  columns={[
                    { key: "key", label: "Key" },
                    { key: "title", label: "Title" },
                    { key: "severity", label: "Severity" },
                  ]}
                  rows={attention.open_incidents.map((incident) => ({
                    __key: incident.incident_key,
                    key: <span className="mono">{incident.incident_key}</span>,
                    title: incident.title,
                    severity: <Status value={incident.severity} />,
                  }))}
                />
              </div>
            ) : null}
          </Card>
        </div>
      </section>

      {/* -------------------------------------------------- agent operations */}
      <section aria-labelledby="ops-heading" className="stack">
        <h2 id="ops-heading">Agent operations · last 24 hours</h2>
        <div className="grid grid-4">
          <Stat
            label="Runs"
            value={ops.runs.total}
            note={`${ops.runs.succeeded} succeeded · ${ops.runs.failed} failed`}
          />
          <Stat
            label="Success rate"
            value={
              ops.runs.success_rate === null
                ? "—"
                : `${(ops.runs.success_rate * 100).toFixed(1)}%`
            }
            note={`p95 ${formatDuration(ops.runs.p95_duration_ms)}`}
          />
          <Stat
            label="Tool calls"
            value={ops.tools.total}
            note={`${ops.tools.denied} denied by the gateway`}
          />
          <Stat
            label="Policy decisions"
            value={ops.policy.decisions}
            note={`${ops.policy.denied} denied · ${ops.policy.escalated_to_approval} escalated`}
          />
          <Stat
            label="Retrieval queries"
            value={ops.retrieval.queries}
            note={`${ops.retrieval.chunks_withheld_by_acl} chunks withheld by ACL`}
          />
          <Stat
            label="Security findings"
            value={ops.security.findings}
            note={`${ops.security.severe_findings} high or critical`}
          />
          <Stat
            label="Awaiting approval"
            value={data.business_pulse.runs_awaiting_approval}
            note="runs held for a human decision"
          />
          <Stat
            label="Platform spend · 7d"
            value={formatCost(data.business_pulse.cost_usd)}
            note="model and tool cost recorded"
          />
        </div>
      </section>
    </div>
  );
}
