import { useApi } from "@/lib/use-api";
import { Card, DataTable, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Prompt {
  prompt_key: string;
  purpose: string;
  owning_agent_key: string;
  current_version: string;
  version: string | null;
  deployment_status: string | null;
  body_hash: string | null;
  evaluation_score: number | null;
  effective_from: string | null;
}

export default function PromptsPage() {
  const { data, error, status , loading } = useApi<{ prompts: Prompt[] }>("/api/v1/prompts");
  if (loading) return <div className="empty">Loading...</div>;


  return (
    <div className="stack">
      <div>
        <h1>Prompt registry</h1>
        <p className="page-lede">
          Prompts are controlled production assets: versioned, hashed and approved.
          A body edited in place without a version bump fails to resolve rather than
          being served quietly.
        </p>
      </div>
      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="the prompt registry" />
      ) : (
        <Card>
          <DataTable
            caption="Registered prompts"
            columns={[
              { key: "prompt", label: "Prompt" },
              { key: "agent", label: "Owning agent" },
              { key: "version", label: "Version" },
              { key: "state", label: "Deployment" },
              { key: "hash", label: "Body hash" },
              { key: "effective", label: "Effective from" },
            ]}
            rows={data.prompts.map((prompt) => ({
              __key: prompt.prompt_key,
              prompt: (
                <>
                  <strong className="mono">{prompt.prompt_key}</strong>
                  <div className="muted" style={{ fontSize: 12 }}>{prompt.purpose}</div>
                </>
              ),
              agent: <span className="mono">{prompt.owning_agent_key || "—"}</span>,
              version: <span className="mono">{prompt.version ?? prompt.current_version}</span>,
              state: <Status value={prompt.deployment_status ?? undefined} />,
              hash: <span className="mono muted">{prompt.body_hash?.slice(0, 12) ?? "—"}</span>,
              effective: <span className="mono">{formatWhen(prompt.effective_from)}</span>,
            }))}
          />
        </Card>
      )}
    </div>
  );
}
