import { useState } from "react";
import { useApi } from "@/lib/use-api";
import { Card, DataTable, Empty, Notice, Stat, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiFetch } from "@/lib/api";

interface SecurityPosture {
  findings: {
    finding_type: string;
    severity: string;
    source: string;
    detail: Record<string, unknown>;
    blocked: boolean;
    created_at: string;
  }[];
  kill_switches: {
    scope: string;
    target_key: string;
    engaged: boolean;
    reason: string;
    engaged_at: string | null;
  }[];
  denials_by_stage: { denial_stage: string; n: number }[];
}

export default function SecurityPage() {
  const { data, error, status, loading, refetch } = useApi<SecurityPosture>("/api/v1/security");
  const [reason, setReason] = useState("");
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [changing, setChanging] = useState(false);
  if (loading) return <div className="empty">Loading...</div>;


  if (!data) {
    return (
      <>
        <h1>Security</h1>
        <SurfaceError error={error ?? ""} status={status} what="security posture" />
      </>
    );
  }

  const engaged = data.kill_switches.filter((k) => k.engaged);
  const severe = data.findings.filter((f) => ["HIGH", "CRITICAL"].includes(f.severity));
  async function setKillSwitch(switchRow: SecurityPosture["kill_switches"][number], engaged: boolean) {
    if (engaged && reason.trim().length < 5) {
      setMutationError("Enter a reason of at least 5 characters before engaging a kill switch.");
      return;
    }
    if (!window.confirm(`${engaged ? "Engage" : "Release"} the ${switchRow.scope} kill switch?`)) return;
    setChanging(true);
    setMutationError(null);
    try {
      await apiFetch("/api/v1/security/kill-switches", {
        method: "POST",
        body: JSON.stringify({ ...switchRow, engaged, reason: reason.trim() || "Operator release" }),
      });
      setReason("");
      refetch();
    } catch (cause) {
      setMutationError(cause instanceof Error ? cause.message : "Kill-switch update failed.");
    } finally {
      setChanging(false);
    }
  }

  return (
    <div className="stack">
      <div>
        <h1>Security</h1>
        <p className="page-lede">
          Where the platform refused something, and why. Denials are grouped by the
          gateway stage that produced them, so a spike is diagnosable rather than
          just alarming.
        </p>
      </div>

      {engaged.length > 0 ? (
        <Notice tone="danger">
          <strong>{engaged.length} kill switch(es) engaged.</strong>{" "}
          {engaged.map((k) => `${k.scope}${k.target_key ? `:${k.target_key}` : ""}`).join(", ")}
        </Notice>
      ) : null}

      <div className="grid grid-3">
        <Stat
          label="Security findings"
          value={data.findings.length}
          note={`${severe.length} high or critical`}
        />
        <Stat
          label="Gateway denials · 7d"
          value={data.denials_by_stage.reduce((sum, row) => sum + Number(row.n), 0)}
          note="tool calls refused before execution"
        />
        <Stat
          label="Kill switches engaged"
          value={engaged.length}
          note={`${data.kill_switches.length} configured`}
        />
      </div>

      <div className="grid grid-2">
        <Card title="Denials by gateway stage">
          {data.denials_by_stage.length === 0 ? (
            <Empty>No tool call has been denied in the last seven days.</Empty>
          ) : (
            <DataTable
              caption="Tool-call denials by stage"
              columns={[
                { key: "stage", label: "Stage" },
                { key: "count", label: "Denials", numeric: true },
              ]}
              rows={data.denials_by_stage.map((row) => ({
                __key: row.denial_stage,
                stage: <span className="mono">{row.denial_stage || "unspecified"}</span>,
                count: row.n,
              }))}
            />
          )}
        </Card>

        <Card title="Kill switches">
          <div className="field">
            <label htmlFor="kill-switch-reason">Change reason</label>
            <input id="kill-switch-reason" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Required when engaging" />
          </div>
          {mutationError ? <Notice tone="danger">{mutationError}</Notice> : null}
          <DataTable
            caption="Kill switch state"
            empty="No kill switch is configured."
            columns={[
              { key: "scope", label: "Scope" },
              { key: "target", label: "Target" },
              { key: "state", label: "State" },
              { key: "reason", label: "Reason" },
              { key: "action", label: "Action" },
            ]}
            rows={data.kill_switches.map((switchRow, index) => ({
              __key: index,
              scope: <span className="mono">{switchRow.scope}</span>,
              target: <span className="mono muted">{switchRow.target_key || "all"}</span>,
              state: switchRow.engaged ? (
                <Status value="ENGAGED" tone="danger" />
              ) : (
                <Status value="released" tone="ok" />
              ),
              reason: <span className="muted">{switchRow.reason || "—"}</span>,
              action: (
                <button className={`btn ${switchRow.engaged ? "" : "btn-primary"}`} disabled={changing} onClick={() => setKillSwitch(switchRow, !switchRow.engaged)}>
                  {switchRow.engaged ? "Release" : "Engage"}
                </button>
              ),
            }))}
          />
        </Card>
      </div>

      <Card title="Security findings">
        <DataTable
          caption="Recorded security findings"
          empty="No security finding has been recorded."
          columns={[
            { key: "type", label: "Finding" },
            { key: "severity", label: "Severity" },
            { key: "source", label: "Source" },
            { key: "blocked", label: "Blocked" },
            { key: "when", label: "When" },
          ]}
          rows={data.findings.map((finding, index) => ({
            __key: index,
            type: <span className="mono">{finding.finding_type}</span>,
            severity: <Status value={finding.severity} />,
            source: <span className="mono muted">{finding.source}</span>,
            blocked: finding.blocked ? (
              <span className="badge badge-ok">blocked</span>
            ) : (
              <span className="badge badge-warn">recorded only</span>
            ),
            when: <span className="mono">{formatWhen(finding.created_at)}</span>,
          }))}
        />
      </Card>
    </div>
  );
}
