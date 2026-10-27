import { useApi } from "@/lib/use-api";
import { Link, useParams } from 'wouter';

import {
  Card,
  DataTable,
  Empty,
  Stat,
  Status,
  SurfaceError,
  formatCost,
  formatWhen,
} from "@/components/ui";
import { apiTry } from "@/lib/api";

interface AgentDetail {
  agent: {
    agent_key: string;
    name: string;
    owner_team: string;
    risk_class: string;
    max_autonomy: string;
    status: string;
    current_version: string;
    contract: Record<string, any>;
    contract_hash: string;
    published_at: string | null;
  };
  recent_runs: {
    id: string;
    objective: string;
    status: string;
    risk_class: string;
    cost_usd: number;
    created_at: string;
  }[];
  evaluations: {
    suite_key: string;
    score: number;
    threshold: number;
    passed: boolean;
    case_count: number;
    created_at: string;
  }[];
}

export default function AgentDetailPage() {
  const { agentKey = "" } = useParams<{ agentKey: string }>();
  const { data, error, status , loading } = useApi<AgentDetail>(`/api/v1/agents/${agentKey}`);
  if (loading) return <div className="empty">Loading...</div>;


  if (!data) {
    return (
      <>
        <h1>Agent</h1>
        <SurfaceError error={error ?? ""} status={status} what="this agent" />
      </>
    );
  }

  const contract = data.agent.contract;
  const limits = contract.limits ?? {};
  const requirements = contract.requirements ?? {};

  return (
    <div className="stack">
      <div>
        <p className="mono muted" style={{ marginBottom: 4 }}>
          <Link href="/agents">Agents</Link> / {agentKey}
        </p>
        <h1>{data.agent.name}</h1>
        <div className="row" style={{ marginTop: 8 }}>
          <Status value={data.agent.status} />
          <Status value={data.agent.risk_class} />
          <span className="badge badge-muted">max {data.agent.max_autonomy}</span>
          <span className="mono muted">
            v{data.agent.current_version} · hash {data.agent.contract_hash.slice(0, 12)}
          </span>
          <span className="mono muted">owner {data.agent.owner_team}</span>
        </div>
      </div>

      <Card title="Business purpose">
        <p style={{ margin: 0 }}>{contract.purpose?.business_purpose}</p>
        <div className="grid grid-2" style={{ marginTop: 14 }}>
          <div>
            <h3>Permitted</h3>
            <ul className="mono muted" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
              {(contract.purpose?.allowed ?? []).map((item: string) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
          <div>
            <h3>Prohibited</h3>
            <ul className="mono muted" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
              {(contract.purpose?.prohibited ?? []).length === 0 ? (
                <li>none declared</li>
              ) : (
                (contract.purpose?.prohibited ?? []).map((item: string) => (
                  <li key={item}>{item}</li>
                ))
              )}
            </ul>
          </div>
        </div>
      </Card>

      <div className="grid grid-4">
        <Stat label="Token budget" value={limits.token_budget?.toLocaleString() ?? "—"} />
        <Stat label="Cost budget" value={formatCost(limits.cost_budget_usd)} />
        <Stat label="Runtime limit" value={`${limits.max_runtime_seconds ?? "—"} s`} />
        <Stat
          label="Tool-call limit"
          value={limits.max_tool_calls ?? "—"}
          note={limits.max_tool_calls === 0 ? "holds no tool authority" : undefined}
        />
      </div>

      <div className="grid grid-3">
        <Card title="Allowed tools">
          {(contract.tools?.allowed ?? []).length === 0 ? (
            <Empty>None — this agent dispatches, it does not execute.</Empty>
          ) : (
            <ul className="mono" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
              {(contract.tools?.allowed ?? []).map((tool: string) => (
                <li key={tool}>{tool}</li>
              ))}
            </ul>
          )}
          {(contract.tools?.denied ?? []).length > 0 ? (
            <>
              <h3 style={{ marginTop: 12 }}>Explicitly denied</h3>
              <ul
                className="mono"
                style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--danger)" }}
              >
                {(contract.tools?.denied ?? []).map((tool: string) => (
                  <li key={tool}>{tool}</li>
                ))}
              </ul>
            </>
          ) : null}
        </Card>

        <Card title="Allowed skills">
          <ul className="mono" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
            {(contract.skills?.allowed ?? []).map((skill: string) => (
              <li key={skill}>{skill}</li>
            ))}
          </ul>
        </Card>

        <Card title="Data domains">
          <h3>Permitted</h3>
          <ul className="mono" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
            {(contract.data?.permitted_domains ?? []).map((domain: string) => (
              <li key={domain}>{domain}</li>
            ))}
          </ul>
          <h3 style={{ marginTop: 12 }}>Prohibited</h3>
          <ul
            className="mono"
            style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--danger)" }}
          >
            {(contract.data?.prohibited_domains ?? []).map((domain: string) => (
              <li key={domain}>{domain}</li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title="Consequential actions requiring human authorisation">
        {(contract.autonomy?.consequential_actions ?? []).length === 0 ? (
          <Empty>This agent proposes no consequential actions.</Empty>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {(contract.autonomy?.consequential_actions ?? []).map((action: string) => (
              <li key={action} className="mono">
                {action}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid grid-2">
        <Card title="Requirements">
          <DataTable
            caption="Contract requirements"
            columns={[
              { key: "requirement", label: "Requirement" },
              { key: "value", label: "Value" },
            ]}
            rows={[
              {
                __key: "citations",
                requirement: "Citations",
                value: requirements.citations ? "required" : "not required",
              },
              {
                __key: "provenance",
                requirement: "Provenance",
                value: requirements.provenance ? "required" : "not required",
              },
              {
                __key: "evaluation",
                requirement: "Evaluation",
                value: `min score ${requirements.evaluation?.min_score ?? "—"}`,
              },
              {
                __key: "suites",
                requirement: "Evaluation suites",
                value: (
                  <span className="mono">
                    {(requirements.evaluation?.suites ?? []).join(", ") || "—"}
                  </span>
                ),
              },
            ]}
          />
        </Card>

        <Card title="Evaluations">
          {data.evaluations.length === 0 ? (
            <Empty>
              No evaluation has been recorded for this agent version. Evaluations run
              in the assurance pipeline and are required before promotion.
            </Empty>
          ) : (
            <DataTable
              caption="Evaluation history"
              columns={[
                { key: "suite", label: "Suite" },
                { key: "score", label: "Score", numeric: true },
                { key: "passed", label: "Result" },
              ]}
              rows={data.evaluations.map((evaluation, index) => ({
                __key: index,
                suite: <span className="mono">{evaluation.suite_key}</span>,
                score: `${evaluation.score} / ${evaluation.threshold}`,
                passed: <Status value={evaluation.passed ? "PASSED" : "FAILED"} />,
              }))}
            />
          )}
        </Card>
      </div>

      <Card title="Recent runs">
        <DataTable
          caption="Recent runs owned by this agent"
          empty="This agent has not run yet."
          columns={[
            { key: "objective", label: "Objective" },
            { key: "status", label: "Status" },
            { key: "risk", label: "Risk" },
            { key: "cost", label: "Cost", numeric: true },
            { key: "when", label: "When" },
          ]}
          rows={data.recent_runs.map((run) => ({
            __key: run.id,
            objective: <Link href={`/runs/${run.id}`}>{run.objective.slice(0, 64)}</Link>,
            status: <Status value={run.status} />,
            risk: <Status value={run.risk_class} />,
            cost: formatCost(run.cost_usd),
            when: <span className="mono">{formatWhen(run.created_at)}</span>,
          }))}
        />
      </Card>
    </div>
  );
}
