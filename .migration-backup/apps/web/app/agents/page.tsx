import Link from "next/link";

import { Card, DataTable, Status, SurfaceError, formatCost } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Agent {
  agent_key: string;
  name: string;
  owner_team: string;
  business_purpose: string;
  risk_class: string;
  max_autonomy: string;
  status: string;
  current_version: string;
  contract_hash: string;
  allowed_tools: string[];
  allowed_skills: string[];
  allowed_models: string[];
  max_classification: string;
  cost_budget_usd: number;
  max_tool_calls: number;
  slo_success_rate: number;
  run_count: number;
}

export default async function AgentsPage() {
  const { data, error, status } = await apiTry<{ agents: Agent[] }>("/api/v1/agents");

  return (
    <div className="stack">
      <div>
        <h1>Agents</h1>
        <p className="page-lede">
          Each agent is a signed contract, not a prompt. The contract bounds which
          tools, skills, models and data domains it may touch, and the runtime pins
          the published version for the whole run.
        </p>
      </div>

      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="agents" />
      ) : (
        <Card>
          <DataTable
            caption="Governed domain agents"
            columns={[
              { key: "agent", label: "Agent" },
              { key: "owner", label: "Owner" },
              { key: "risk", label: "Risk" },
              { key: "autonomy", label: "Max autonomy" },
              { key: "data", label: "Data ceiling" },
              { key: "grants", label: "Grants" },
              { key: "budget", label: "Budget", numeric: true },
              { key: "runs", label: "Runs", numeric: true },
            ]}
            rows={data.agents.map((agent) => ({
              __key: agent.agent_key,
              agent: (
                <>
                  <Link href={`/agents/${agent.agent_key}`}>{agent.name}</Link>
                  <div className="mono muted">
                    {agent.agent_key} v{agent.current_version}
                  </div>
                </>
              ),
              owner: agent.owner_team,
              risk: <Status value={agent.risk_class} />,
              autonomy: <span className="mono">{agent.max_autonomy}</span>,
              data: <Status value={agent.max_classification} />,
              grants: (
                <span className="mono muted">
                  {agent.allowed_tools?.length ?? 0} tools · {agent.allowed_skills?.length ?? 0} skills
                </span>
              ),
              budget: formatCost(agent.cost_budget_usd),
              runs: agent.run_count,
            }))}
          />
        </Card>
      )}
    </div>
  );
}
