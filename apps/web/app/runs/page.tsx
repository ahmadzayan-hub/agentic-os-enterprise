import Link from "next/link";

import {
  Card,
  DataTable,
  Status,
  SurfaceError,
  formatCost,
  formatDuration,
  formatWhen,
} from "@/components/ui";
import { apiTry } from "@/lib/api";
import type { RunSummary } from "@/lib/types";

const STATUSES = [
  "",
  "SUCCEEDED",
  "FAILED",
  "RUNNING",
  "AWAITING_APPROVAL",
  "PLANNING",
  "CANCELLED",
];

export default async function RunsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const params = await searchParams;
  const query = params.status ? `?status=${encodeURIComponent(params.status)}` : "";
  const { data, error, status } = await apiTry<{ runs: RunSummary[] }>(
    `/api/v1/runs${query}`,
  );

  return (
    <div className="stack">
      <div>
        <h1>Runs</h1>
        <p className="page-lede">
          Every governed execution, with the plan it followed, the policy decisions it
          triggered and the evidence it produced.
        </p>
      </div>

      <nav aria-label="Filter runs by status" className="row">
        {STATUSES.map((value) => (
          <Link
            key={value || "all"}
            href={value ? `/runs?status=${value}` : "/runs"}
            className={`badge badge-${(params.status ?? "") === value ? "info" : "muted"}`}
            aria-current={(params.status ?? "") === value ? "true" : undefined}
          >
            {value || "All"}
          </Link>
        ))}
      </nav>

      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="runs" />
      ) : (
        <Card>
          <DataTable
            caption="Runs"
            empty="No runs match this filter."
            columns={[
              { key: "objective", label: "Objective" },
              { key: "status", label: "Status" },
              { key: "agent", label: "Agent" },
              { key: "risk", label: "Risk" },
              { key: "autonomy", label: "Autonomy" },
              { key: "steps", label: "Steps", numeric: true },
              { key: "cost", label: "Cost", numeric: true },
              { key: "duration", label: "Duration", numeric: true },
              { key: "created", label: "Created" },
            ]}
            rows={data.runs.map((run) => ({
              __key: run.id,
              objective: (
                <Link href={`/runs/${run.id}`}>
                  {run.objective.length > 72
                    ? `${run.objective.slice(0, 72)}…`
                    : run.objective}
                </Link>
              ),
              status: (
                <>
                  <Status value={run.status} />
                  {run.pending_approvals > 0 ? (
                    <>
                      {" "}
                      <span className="badge badge-warn">
                        {run.pending_approvals} approval
                        {run.pending_approvals === 1 ? "" : "s"}
                      </span>
                    </>
                  ) : null}
                </>
              ),
              agent: <span className="mono">{run.owner_agent_key}</span>,
              risk: <Status value={run.risk_class} />,
              autonomy: <span className="mono">{run.autonomy_level}</span>,
              steps: run.step_count,
              cost: formatCost(run.cost_usd),
              duration: formatDuration(run.duration_ms),
              created: <span className="mono">{formatWhen(run.created_at)}</span>,
            }))}
          />
        </Card>
      )}
    </div>
  );
}
