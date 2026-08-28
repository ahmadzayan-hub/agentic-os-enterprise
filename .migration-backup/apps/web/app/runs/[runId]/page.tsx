import Link from "next/link";

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

interface RunDetail {
  run: Record<string, unknown>;
  plan: {
    version: number;
    planner: string;
    steps: { index: number; key: string; skill: string; tool: string | null; description: string; requires_approval: boolean }[];
    plan_hash: string;
    validated: boolean;
    validation_errors: { code: string; message: string; step_index: number | null }[];
    rationale: string;
  }[];
  steps: {
    step_index: number;
    step_key: string;
    step_type: string;
    agent_key: string;
    skill_key: string;
    tool_key: string;
    status: string;
    attempt: number;
    error_class: string | null;
    error_message: string | null;
    cost_usd: number;
    input_tokens: number;
    output_tokens: number;
    latency_ms: number | null;
    output: unknown;
  }[];
  policy_decisions: {
    action: string;
    resource: string;
    effect: string;
    reason: string;
    evaluated_at: string;
  }[];
  risk_assessments: {
    action: string;
    risk_class: string;
    risk_score: number;
    factors: { name: string; weight: number; detail: string }[];
    reversibility: string;
    required_autonomy: string;
  }[];
  tool_calls: {
    tool_key: string;
    agent_key: string;
    gateway_decision: string;
    denial_stage: string;
    denial_reason: string;
    verification_status: string;
    latency_ms: number | null;
  }[];
  approvals: {
    id: string;
    action: string;
    status: string;
    mode: string;
    risk_class: string;
    reason: string;
  }[];
  citations: {
    chunk_id: string;
    document_id: string;
    title: string | null;
    section_path: string | null;
    snippet: string | null;
    verified: boolean;
  }[];
  model_calls: {
    provider: string;
    model_key: string;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  }[];
  trace: { name: string; kind: string; status: string; duration_ms: number | null }[];
  audit: {
    sequence_no: number;
    category: string;
    action: string;
    outcome: string;
    occurred_at: string;
  }[];
}

export default async function RunDetailPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;
  const { data, error, status } = await apiTry<RunDetail>(`/api/v1/runs/${runId}`);

  if (!data) {
    return (
      <>
        <h1>Run</h1>
        <SurfaceError error={error ?? ""} status={status} what="this run" />
      </>
    );
  }

  const run = data.run as Record<string, any>;
  const plan = data.plan[0];
  const result = run.result as { answer?: string; citations?: string[]; grounded?: boolean } | null;
  const totalTokens = data.model_calls.reduce(
    (sum, call) => sum + call.input_tokens + call.output_tokens,
    0,
  );

  return (
    <div className="stack">
      <div>
        <p className="mono muted" style={{ marginBottom: 4 }}>
          <Link href="/runs">Runs</Link> / {runId.slice(0, 8)}
        </p>
        <h1>{String(run.objective)}</h1>
        <div className="row" style={{ marginTop: 8 }}>
          <Status value={String(run.status)} />
          <Status value={String(run.risk_class)} />
          <span className="badge badge-muted">autonomy {String(run.autonomy_level)}</span>
          <span className="badge badge-muted">{String(run.classification)}</span>
          <span className="mono muted">agent {String(run.owner_agent_key)}</span>
          <span className="mono muted">
            requested by {String(run.requested_by_email ?? "—")}
          </span>
        </div>
      </div>

      {run.error_message ? (
        <Notice tone="danger">
          <strong>{String(run.error_class)}</strong>: {String(run.error_message)}
        </Notice>
      ) : null}

      <div className="grid grid-4">
        <Stat label="Steps" value={data.steps.length} note={`${data.tool_calls.length} tool calls`} />
        <Stat label="Cost" value={formatCost(Number(run.cost_usd))} note={`${totalTokens} tokens`} />
        <Stat label="Duration" value={formatDuration(run.duration_ms as number)} />
        <Stat
          label="Confidence"
          value={run.confidence === null ? "—" : `${(Number(run.confidence) * 100).toFixed(0)}%`}
          note={`risk score ${Number(run.risk_score).toFixed(2)}`}
        />
      </div>

      {/* ------------------------------------------------------------ result */}
      {result?.answer ? (
        <Card title="Result">
          <p style={{ marginTop: 0 }}>{result.answer}</p>
          <div className="row">
            {result.grounded ? (
              <span className="badge badge-ok">grounded in {result.citations?.length ?? 0} sources</span>
            ) : (
              <span className="badge badge-warn">no citations — treat as unsupported</span>
            )}
          </div>
        </Card>
      ) : null}

      {/* -------------------------------------------------------------- plan */}
      <Card
        title="Plan"
        action={
          plan ? (
            <span className="mono muted">
              {plan.planner} · hash {plan.plan_hash.slice(0, 12)}
            </span>
          ) : null
        }
      >
        {!plan ? (
          <Empty>No plan was recorded for this run.</Empty>
        ) : (
          <>
            <div className="row" style={{ marginBottom: 12 }}>
              {plan.validated ? (
                <span className="badge badge-ok">validated</span>
              ) : (
                <span className="badge badge-danger">rejected by the plan validator</span>
              )}
              <span className="muted">{plan.rationale}</span>
            </div>

            {plan.validation_errors.length > 0 ? (
              <div style={{ marginBottom: 12 }}>
                <Notice tone="danger">
                  <strong>The plan did not pass validation.</strong>
                  <ul style={{ margin: "8px 0 0", paddingLeft: 20 }}>
                    {plan.validation_errors.map((issue, index) => (
                      <li key={index}>
                        <span className="mono">{issue.code}</span> — {issue.message}
                      </li>
                    ))}
                  </ul>
                </Notice>
              </div>
            ) : null}

            <ol className="timeline">
              {plan.steps.map((step) => {
                const executed = data.steps.find((s) => s.step_index === step.index);
                return (
                  <li key={step.key} data-state={executed?.status ?? "PENDING"}>
                    <div className="row">
                      <strong>{step.key}</strong>
                      <Status value={executed?.status ?? "not executed"} />
                      {step.requires_approval ? (
                        <span className="badge badge-warn">requires approval</span>
                      ) : null}
                    </div>
                    <div className="muted" style={{ fontSize: 13 }}>
                      {step.description}
                    </div>
                    <div className="mono muted" style={{ marginTop: 3 }}>
                      skill {step.skill || "—"} · tool {step.tool || "—"}
                      {executed
                        ? ` · ${formatDuration(executed.latency_ms)} · ${formatCost(executed.cost_usd)}`
                        : ""}
                    </div>
                    {executed?.error_message ? (
                      <div className="mono" style={{ color: "var(--danger)", marginTop: 4 }}>
                        {executed.error_class}: {executed.error_message}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          </>
        )}
      </Card>

      {/* ------------------------------------------------------- governance */}
      <div className="grid grid-2">
        <Card title="Policy decisions">
          <DataTable
            caption="Policy decisions for this run"
            empty="No policy decision was recorded."
            columns={[
              { key: "action", label: "Action" },
              { key: "effect", label: "Effect" },
              { key: "reason", label: "Reason" },
            ]}
            rows={data.policy_decisions.map((decision, index) => ({
              __key: index,
              action: <span className="mono">{decision.action}</span>,
              effect: <Status value={decision.effect} />,
              reason: decision.reason,
            }))}
          />
        </Card>

        <Card title="Risk assessments">
          {data.risk_assessments.length === 0 ? (
            <Empty>No risk assessment was recorded.</Empty>
          ) : (
            <div className="stack" style={{ gap: 10 }}>
              {data.risk_assessments.map((assessment, index) => (
                <div key={index}>
                  <div className="row">
                    <span className="mono">{assessment.action}</span>
                    <Status value={assessment.risk_class} />
                    <span className="badge badge-muted">
                      needs {assessment.required_autonomy}
                    </span>
                    <span className="badge badge-muted">{assessment.reversibility}</span>
                    <span className="mono muted">score {assessment.risk_score}</span>
                  </div>
                  {assessment.factors.length > 0 ? (
                    <ul className="mono muted" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                      {assessment.factors.map((factor, i) => (
                        <li key={i}>
                          {factor.name} +{factor.weight} — {factor.detail}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Tool calls">
          <DataTable
            caption="Tool calls made during this run"
            empty="No tool was invoked."
            columns={[
              { key: "tool", label: "Tool" },
              { key: "decision", label: "Gateway" },
              { key: "verification", label: "Verified" },
              { key: "latency", label: "Latency", numeric: true },
            ]}
            rows={data.tool_calls.map((call, index) => ({
              __key: index,
              tool: <span className="mono">{call.tool_key}</span>,
              decision: (
                <>
                  <Status value={call.gateway_decision} />
                  {call.denial_stage ? (
                    <div className="mono muted">at {call.denial_stage}</div>
                  ) : null}
                </>
              ),
              verification: <Status value={call.verification_status} />,
              latency: formatDuration(call.latency_ms),
            }))}
          />
        </Card>

        <Card title="Model calls">
          <DataTable
            caption="Model calls made during this run"
            empty="No model was called."
            columns={[
              { key: "model", label: "Model" },
              { key: "provider", label: "Provider" },
              { key: "tokens", label: "Tokens", numeric: true },
              { key: "cost", label: "Cost", numeric: true },
            ]}
            rows={data.model_calls.map((call, index) => ({
              __key: index,
              model: <span className="mono">{call.model_key}</span>,
              provider: call.provider,
              tokens: call.input_tokens + call.output_tokens,
              cost: formatCost(call.cost_usd),
            }))}
          />
        </Card>
      </div>

      {/* -------------------------------------------------------- evidence */}
      <Card title="Citations">
        {data.citations.length === 0 ? (
          <Empty>This run produced no citations.</Empty>
        ) : (
          <div className="stack" style={{ gap: 10 }}>
            {data.citations.map((citation) => (
              <div key={citation.chunk_id} className="card" style={{ padding: 12 }}>
                <div className="row">
                  <strong>{citation.title ?? "Untitled source"}</strong>
                  {citation.verified ? (
                    <span className="badge badge-ok">verified</span>
                  ) : (
                    <span className="badge badge-warn">unverified</span>
                  )}
                  {citation.section_path ? (
                    <span className="mono muted">{citation.section_path}</span>
                  ) : null}
                </div>
                {citation.snippet ? (
                  <p className="muted" style={{ margin: "6px 0 0", fontSize: 13 }}>
                    {citation.snippet.slice(0, 320)}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Card>

      {data.approvals.length > 0 ? (
        <Card title="Approvals" action={<Link href="/approvals">Approvals</Link>}>
          <DataTable
            caption="Approvals raised by this run"
            columns={[
              { key: "action", label: "Action" },
              { key: "status", label: "Status" },
              { key: "mode", label: "Mode" },
              { key: "risk", label: "Risk" },
            ]}
            rows={data.approvals.map((approval) => ({
              __key: approval.id,
              action: <span className="mono">{approval.action}</span>,
              status: <Status value={approval.status} />,
              mode: approval.mode,
              risk: <Status value={approval.risk_class} />,
            }))}
          />
        </Card>
      ) : null}

      <Card title="Audit trail">
        <DataTable
          caption="Audit ledger entries for this run"
          empty="No audit entries."
          columns={[
            { key: "seq", label: "#", numeric: true },
            { key: "category", label: "Category" },
            { key: "action", label: "Action" },
            { key: "outcome", label: "Outcome" },
            { key: "when", label: "When" },
          ]}
          rows={data.audit.map((entry) => ({
            __key: entry.sequence_no,
            seq: entry.sequence_no,
            category: <span className="mono">{entry.category}</span>,
            action: <span className="mono">{entry.action}</span>,
            outcome: <Status value={entry.outcome} />,
            when: <span className="mono">{formatWhen(entry.occurred_at)}</span>,
          }))}
        />
      </Card>
    </div>
  );
}
