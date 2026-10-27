import { useApi } from "@/lib/use-api";
import { Card, DataTable, Notice, Status, SurfaceError } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Capabilities {
  agents: string[];
  skills: { deterministic: string[]; model_backed: string[] };
  tools: { implemented: string[]; declared_not_implemented: string[] };
  models: Record<string, { provider: string; deployment: string; approval_state: string }>;
  external_model_providers_enabled: boolean;
  embedding_provider: string;
  policy_mode: string;
  kms_backend: string;
  secret_backend: string;
}

/**
 * The honesty surface: what this deployment can actually do right now, including
 * everything it cannot. Declared-but-unbuilt capabilities appear here rather
 * than in navigation, so nothing in the product is a dead control.
 */
export default function CapabilitiesPage() {
  const { data, error, status , loading } = useApi<Capabilities>("/api/v1/capabilities");
  if (loading) return <div className="empty">Loading...</div>;


  if (!data) {
    return (
      <>
        <h1>Capabilities</h1>
        <SurfaceError error={error ?? ""} status={status} what="capabilities" />
      </>
    );
  }

  return (
    <div className="stack">
      <div>
        <h1>Capabilities</h1>
        <p className="page-lede">
          What this deployment can do, and what it cannot. Anything listed as not
          implemented is refused by the gateway with a 501 — it never returns
          fabricated data.
        </p>
      </div>

      {!data.external_model_providers_enabled ? (
        <Notice tone="info">
          External model providers are <strong>disabled</strong>. Inference is served
          by the local deterministic engine, which is rule-based and not generative.
          Its outputs are labelled as such on every run.
        </Notice>
      ) : null}

      <div className="grid grid-2">
        <Card title={`Tools implemented (${data.tools.implemented.length})`}>
          <ul className="mono" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
            {data.tools.implemented.map((tool) => (
              <li key={tool}>{tool}</li>
            ))}
          </ul>
        </Card>
        <Card title={`Declared, not implemented (${data.tools.declared_not_implemented.length})`}>
          <ul
            className="mono"
            style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--warn)" }}
          >
            {data.tools.declared_not_implemented.map((tool) => (
              <li key={tool}>{tool}</li>
            ))}
          </ul>
        </Card>
        <Card title={`Deterministic skills (${data.skills.deterministic.length})`}>
          <ul className="mono" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
            {data.skills.deterministic.map((skill) => (
              <li key={skill}>{skill}</li>
            ))}
          </ul>
        </Card>
        <Card title={`Model-backed skills (${data.skills.model_backed.length})`}>
          <ul className="mono" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
            {data.skills.model_backed.map((skill) => (
              <li key={skill}>{skill}</li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title="Models">
        <DataTable
          caption="Registered models and their approval state"
          columns={[
            { key: "model", label: "Model" },
            { key: "provider", label: "Provider" },
            { key: "deployment", label: "Deployment" },
            { key: "approval", label: "Approval" },
          ]}
          rows={Object.entries(data.models).map(([key, model]) => ({
            __key: key,
            model: <span className="mono">{key}</span>,
            provider: model.provider,
            deployment: <span className="mono">{model.deployment}</span>,
            approval: (
              <Status
                value={model.approval_state}
                tone={model.approval_state === "APPROVED" ? "ok" : "warn"}
              />
            ),
          }))}
        />
      </Card>

      <Card title="Runtime configuration">
        <DataTable
          caption="Runtime configuration"
          columns={[
            { key: "setting", label: "Setting" },
            { key: "value", label: "Value" },
          ]}
          rows={[
            {
              __key: "policy",
              setting: "Policy mode",
              value: <Status value={data.policy_mode} tone={data.policy_mode === "enforce" ? "ok" : "warn"} />,
            },
            {
              __key: "kms",
              setting: "KMS backend",
              value: (
                <Status value={data.kms_backend} tone={data.kms_backend === "local" ? "warn" : "ok"} />
              ),
            },
            {
              __key: "secrets",
              setting: "Secret backend",
              value: <span className="mono">{data.secret_backend}</span>,
            },
            {
              __key: "embed",
              setting: "Embedding provider",
              value: <span className="mono">{data.embedding_provider}</span>,
            },
            {
              __key: "agents",
              setting: "Registered agents",
              value: <span className="mono">{data.agents.join(", ")}</span>,
            },
          ]}
        />
      </Card>
    </div>
  );
}
