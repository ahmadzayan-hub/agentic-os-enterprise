import { Card, DataTable, Notice, Status, SurfaceError } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Model {
  model_key: string;
  provider: string;
  deployment: string;
  capabilities: string[];
  max_classification: string;
  context_window: number;
  input_cost_per_1k: number;
  output_cost_per_1k: number;
  p95_latency_ms: number;
  evaluation_score: number | null;
  known_limitations: string;
  residency: string;
  approval_state: string;
}

export default async function ModelsPage() {
  const { data, error, status } = await apiTry<{
    models: Model[];
    usage: { model_key: string; calls: number; tokens: number; cost: number }[];
  }>("/api/v1/models");

  const usage = new Map((data?.usage ?? []).map((u) => [u.model_key, u]));

  return (
    <div className="stack">
      <div>
        <h1>Model registry</h1>
        <p className="page-lede">
          No agent calls a provider. Every inference resolves through the Model
          Gateway, which enforces the contract allowlist, the data-classification
          ceiling and the budget before anything is sent.
        </p>
      </div>

      <Notice tone="info">
        A model in <strong>PENDING</strong> approval is not routable. The gateway
        substitutes an approved deployment and records the substitution on the run,
        rather than failing silently or sending data to an unapproved provider.
      </Notice>

      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="the model registry" />
      ) : (
        <Card>
          <DataTable
            caption="Registered models"
            columns={[
              { key: "model", label: "Model" },
              { key: "deployment", label: "Deployment" },
              { key: "approval", label: "Approval" },
              { key: "data", label: "Data ceiling" },
              { key: "residency", label: "Residency" },
              { key: "cost", label: "Cost / 1k", numeric: true },
              { key: "calls", label: "Calls", numeric: true },
              { key: "limitations", label: "Known limitations" },
            ]}
            rows={data.models.map((model) => ({
              __key: model.model_key,
              model: (
                <>
                  <strong className="mono">{model.model_key}</strong>
                  <div className="muted" style={{ fontSize: 12 }}>{model.provider}</div>
                </>
              ),
              deployment: <span className="mono">{model.deployment}</span>,
              approval: (
                <Status
                  value={model.approval_state}
                  tone={model.approval_state === "APPROVED" ? "ok" : "warn"}
                />
              ),
              data: <Status value={model.max_classification} />,
              residency: <span className="mono">{model.residency}</span>,
              cost: `$${model.input_cost_per_1k} / $${model.output_cost_per_1k}`,
              calls: usage.get(model.model_key)?.calls ?? 0,
              limitations: (
                <span className="muted" style={{ fontSize: 12 }}>
                  {model.known_limitations || "—"}
                </span>
              ),
            }))}
          />
        </Card>
      )}
    </div>
  );
}
