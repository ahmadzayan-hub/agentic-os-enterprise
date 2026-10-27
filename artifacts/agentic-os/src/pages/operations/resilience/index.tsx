import { useApi } from "@/lib/use-api";
import { Card, DataTable, Stat, Status, SurfaceError, formatWhen } from "@/components/ui";

interface BackupRow {
  backup_type: string;
  scope: string;
  artifact_hash: string;
  size_bytes: number;
  status: string;
  started_at: string;
  completed_at: string | null;
}

interface RestoreRow {
  environment: string;
  outcome: string;
  rpo_achieved_seconds: number | null;
  rto_achieved_seconds: number | null;
  verified_rows: number;
  notes: string;
  executed_by: string;
  executed_at: string;
}

function seconds(value: number | null): string {
  if (value === null || value === undefined) return "not measured";
  if (value < 60) return `${value}s`;
  return `${Math.floor(value / 60)}m ${value % 60}s`;
}

function megabytes(value: number): string {
  return `${(value / (1024 * 1024)).toFixed(2)} MB`;
}

export default function ResiliencePage() {
  const resilience = useApi<{ backups: BackupRow[]; restore_tests: RestoreRow[] }>(
    "/api/v1/resilience",
  );

  if (resilience.loading) return <div className="empty">Loading...</div>;

  if (!resilience.data) {
    return (
      <div className="stack">
        <h1>Resilience</h1>
        <SurfaceError
          error={resilience.error ?? ""}
          status={resilience.status}
          what="backup and restore evidence"
        />
      </div>
    );
  }

  const { backups, restore_tests } = resilience.data;
  const latest = restore_tests[0];

  return (
    <div className="stack">
      <div>
        <h1>Resilience</h1>
        <p className="page-lede">
          Recovery objectives are only claimed from an exercise that actually
          ran. Each restore below dumped the live database, restored it into a
          scratch database, compared every table and recomputed each tenant&apos;s
          audit hash chain in the restored copy before being recorded.
        </p>
      </div>

      {latest ? (
        <div className="grid grid-4">
          <Stat label="Last restore" value={<Status value={latest.outcome} />} />
          <Stat
            label="RTO achieved"
            value={seconds(latest.rto_achieved_seconds)}
            note="restore start to verified"
          />
          <Stat
            label="RPO achieved"
            value={seconds(latest.rpo_achieved_seconds)}
            note="data loss window measured against the source"
          />
          <Stat label="Rows verified" value={latest.verified_rows.toLocaleString()} />
        </div>
      ) : (
        <Card title="No restore has been exercised">
          <p className="muted">
            No restore test has been recorded, so no recovery objective is
            evidenced. Run <span className="mono">agentic-dr run</span> with a
            maintenance identity configured.
          </p>
        </Card>
      )}

      <Card title="Restore exercises">
        <DataTable
          caption="Restore exercises"
          empty="No restore exercise has been recorded."
          columns={[
            { key: "environment", label: "Environment" },
            { key: "outcome", label: "Outcome" },
            { key: "rto", label: "RTO" },
            { key: "rpo", label: "RPO" },
            { key: "rows", label: "Rows verified" },
            { key: "by", label: "Executed by" },
            { key: "when", label: "When" },
          ]}
          rows={restore_tests.map((test, index) => ({
            __key: index,
            environment: <span className="mono">{test.environment}</span>,
            outcome: (
              <Status
                value={test.outcome}
                tone={test.outcome === "SUCCESS" ? "ok" : test.outcome === "PARTIAL" ? "warn" : "danger"}
              />
            ),
            rto: <span className="mono">{seconds(test.rto_achieved_seconds)}</span>,
            rpo: <span className="mono">{seconds(test.rpo_achieved_seconds)}</span>,
            rows: <span className="mono">{test.verified_rows.toLocaleString()}</span>,
            by: <span className="mono muted">{test.executed_by}</span>,
            when: <span className="mono">{formatWhen(test.executed_at)}</span>,
          }))}
        />
      </Card>

      <Card title="Backups">
        <DataTable
          caption="Backups"
          empty="No backup has been recorded."
          columns={[
            { key: "type", label: "Type" },
            { key: "scope", label: "Scope" },
            { key: "size", label: "Size" },
            { key: "hash", label: "Artifact hash" },
            { key: "status", label: "Status" },
            { key: "when", label: "Completed" },
          ]}
          rows={backups.map((backup, index) => ({
            __key: index,
            type: <span className="mono">{backup.backup_type}</span>,
            scope: <span className="mono muted">{backup.scope}</span>,
            size: <span className="mono">{megabytes(backup.size_bytes)}</span>,
            hash: <span className="mono muted">{backup.artifact_hash.slice(0, 16)}…</span>,
            status: <Status value={backup.status} tone={backup.status === "COMPLETED" ? "ok" : "warn"} />,
            when: <span className="mono">{formatWhen(backup.completed_at)}</span>,
          }))}
        />
      </Card>
    </div>
  );
}