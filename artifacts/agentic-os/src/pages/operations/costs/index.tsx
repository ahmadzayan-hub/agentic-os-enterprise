import { useApi } from "@/lib/use-api";
import { Card, DataTable, Stat, SurfaceError, formatCost } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Costs {
  window_days: number;
  spend_today_usd: number;
  by_model: {
    model_key: string;
    provider: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  }[];
  by_agent: { agent_key: string; calls: number; cost_usd: number }[];
  budgets: {
    scope: string;
    scope_key: string;
    period: string;
    cost_cap_usd: number;
    token_cap: number;
    hard_stop: boolean;
    fallback_model_key: string;
  }[];
}

export default function CostsPage() {
  const { data, error, status , loading } = useApi<Costs>("/api/v1/costs?window_days=30");
  if (loading) return <div className="empty">Loading...</div>;


  if (!data) {
    return (
      <>
        <h1>Cost</h1>
        <SurfaceError error={error ?? ""} status={status} what="cost data" />
      </>
    );
  }

  const total = data.by_model.reduce((sum, row) => sum + Number(row.cost_usd), 0);

  return (
    <div className="stack">
      <div>
        <h1>Cost governance</h1>
        <p className="page-lede">
          Budgets are checked before an inference runs, not reconciled afterwards. A
          run that would exceed its cap is refused or routed to a cheaper approved
          deployment.
        </p>
      </div>

      <div className="grid grid-3">
        <Stat label="Spend today" value={formatCost(data.spend_today_usd)} />
        <Stat label={`Spend · ${data.window_days}d`} value={formatCost(total)} />
        <Stat label="Budgets configured" value={data.budgets.length} />
      </div>

      <div className="grid grid-2">
        <Card title="By model">
          <DataTable
            caption="Cost by model"
            empty="No model cost recorded."
            columns={[
              { key: "model", label: "Model" },
              { key: "calls", label: "Calls", numeric: true },
              { key: "tokens", label: "Tokens", numeric: true },
              { key: "cost", label: "Cost", numeric: true },
            ]}
            rows={data.by_model.map((row) => ({
              __key: row.model_key,
              model: (
                <>
                  <span className="mono">{row.model_key}</span>
                  <div className="muted" style={{ fontSize: 12 }}>{row.provider}</div>
                </>
              ),
              calls: row.calls,
              tokens: (Number(row.input_tokens) + Number(row.output_tokens)).toLocaleString(),
              cost: formatCost(row.cost_usd),
            }))}
          />
        </Card>

        <Card title="By agent">
          <DataTable
            caption="Cost by agent"
            empty="No agent cost recorded."
            columns={[
              { key: "agent", label: "Agent" },
              { key: "calls", label: "Calls", numeric: true },
              { key: "cost", label: "Cost", numeric: true },
            ]}
            rows={data.by_agent.map((row) => ({
              __key: row.agent_key,
              agent: <span className="mono">{row.agent_key}</span>,
              calls: row.calls,
              cost: formatCost(row.cost_usd),
            }))}
          />
        </Card>
      </div>

      <Card title="Budgets">
        <DataTable
          caption="Configured budgets"
          empty="No budget configured."
          columns={[
            { key: "scope", label: "Scope" },
            { key: "period", label: "Period" },
            { key: "cost", label: "Cost cap", numeric: true },
            { key: "tokens", label: "Token cap", numeric: true },
            { key: "stop", label: "On breach" },
            { key: "fallback", label: "Fallback model" },
          ]}
          rows={data.budgets.map((budget, index) => ({
            __key: index,
            scope: (
              <span className="mono">
                {budget.scope}
                {budget.scope_key ? `:${budget.scope_key}` : ""}
              </span>
            ),
            period: budget.period,
            cost: formatCost(budget.cost_cap_usd),
            tokens: Number(budget.token_cap).toLocaleString(),
            stop: budget.hard_stop ? (
              <span className="badge badge-danger">hard stop</span>
            ) : (
              <span className="badge badge-warn">alert only</span>
            ),
            fallback: <span className="mono muted">{budget.fallback_model_key || "—"}</span>,
          }))}
        />
      </Card>
    </div>
  );
}
