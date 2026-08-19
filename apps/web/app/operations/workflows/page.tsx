import { Card, DataTable, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Workflows {
  workflows: {
    workflow_key: string;
    name: string;
    description: string;
    owner_team: string;
    status: string;
    current_version: number;
    max_concurrent_runs: number;
    definition: { steps: { key: string; type: string }[] } | null;
    definition_hash: string | null;
    run_count: number;
  }[];
  step_types: string[];
}

export default async function WorkflowsPage() {
  const [catalogue, runs] = await Promise.all([
    apiTry<Workflows>("/api/v1/workflows"),
    apiTry<{
      workflow_runs: {
        id: string;
        workflow_key: string;
        status: string;
        current_step: number;
        paused: boolean;
        error_class: string | null;
        started_at: string | null;
      }[];
    }>("/api/v1/workflows/runs?limit=50"),
  ]);

  return (
    <div className="stack">
      <div>
        <h1>Workflows</h1>
        <p className="page-lede">
          Durable and deterministic. State lives in the database, steps are leased,
          retries are bounded, and a failure compensates completed steps in reverse
          order rather than leaving partial work behind.
        </p>
      </div>

      {!catalogue.data ? (
        <SurfaceError error={catalogue.error ?? ""} status={catalogue.status} what="workflows" />
      ) : (
        <Card
          title="Definitions"
          action={
            <span className="mono muted">
              step types: {catalogue.data.step_types.join(", ")}
            </span>
          }
        >
          <DataTable
            caption="Workflow definitions"
            empty="No workflow has been registered."
            columns={[
              { key: "workflow", label: "Workflow" },
              { key: "status", label: "Status" },
              { key: "version", label: "Version", numeric: true },
              { key: "steps", label: "Steps", numeric: true },
              { key: "concurrency", label: "Max concurrent", numeric: true },
              { key: "runs", label: "Runs", numeric: true },
            ]}
            rows={catalogue.data.workflows.map((workflow) => ({
              __key: workflow.workflow_key,
              workflow: (
                <>
                  <strong className="mono">{workflow.workflow_key}</strong>
                  <div className="muted" style={{ fontSize: 12 }}>{workflow.description}</div>
                </>
              ),
              status: <Status value={workflow.status} />,
              version: workflow.current_version,
              steps: workflow.definition?.steps?.length ?? 0,
              concurrency: workflow.max_concurrent_runs,
              runs: workflow.run_count,
            }))}
          />
        </Card>
      )}

      {runs.data ? (
        <Card title="Recent workflow runs">
          <DataTable
            caption="Workflow runs"
            empty="No workflow run has started."
            columns={[
              { key: "workflow", label: "Workflow" },
              { key: "status", label: "Status" },
              { key: "step", label: "Step", numeric: true },
              { key: "error", label: "Error" },
              { key: "started", label: "Started" },
            ]}
            rows={runs.data.workflow_runs.map((run) => ({
              __key: run.id,
              workflow: <span className="mono">{run.workflow_key}</span>,
              status: (
                <>
                  <Status value={run.status} />
                  {run.paused ? (
                    <>
                      {" "}
                      <span className="badge badge-warn">paused</span>
                    </>
                  ) : null}
                </>
              ),
              step: run.current_step,
              error: run.error_class ? <Status value={run.error_class} tone="danger" /> : null,
              started: <span className="mono">{formatWhen(run.started_at)}</span>,
            }))}
          />
        </Card>
      ) : null}
    </div>
  );
}
