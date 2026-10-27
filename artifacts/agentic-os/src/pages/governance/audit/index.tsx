import { useApi } from "@/lib/use-api";
import { Card, DataTable, Notice, Stat, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface AuditEvent {
  sequence_no: number;
  category: string;
  action: string;
  outcome: string;
  resource_type: string;
  resource_id: string;
  agent_id: string | null;
  tool_id: string | null;
  entry_hash: string;
  occurred_at: string;
}

export default function AuditPage() {
  const events = useApi<{ events: AuditEvent[] }>("/api/v1/audit?limit=200");
  const verification = useApi<{
      entries_checked: number;
      intact: boolean;
      broken_at: number | null;
  }>("/api/v1/audit/verify");

  const loading = events.loading || verification.loading;

  if (loading) return <div className="empty">Loading...</div>;

  return (
    <div className="stack">
      <div>
        <h1>Audit ledger</h1>
        <p className="page-lede">
          Application append-only and hash-chained. Every entry includes the prior
          digest, so verification detects alteration or missing links.
        </p>
      </div>

      {verification.data ? (
        <>
          <div className="grid grid-3">
            <Stat label="Entries verified" value={verification.data.entries_checked} />
            <Stat
              label="Chain integrity"
              value={verification.data.intact ? "Intact" : "BROKEN"}
              note={
                verification.data.intact
                  ? "every link recomputed successfully"
                  : `first break at sequence ${verification.data.broken_at}`
              }
            />
            <Stat label="Records shown" value={events.data?.events.length ?? 0} />
          </div>
          {!verification.data.intact ? (
            <Notice tone="danger">
              <strong>The audit chain is broken.</strong> The first invalid link is at
              sequence {verification.data.broken_at}. Treat every subsequent entry as
              untrustworthy and escalate immediately.
            </Notice>
          ) : null}
        </>
      ) : (
        <SurfaceError
          error={verification.error ?? ""}
          status={verification.status}
          what="chain verification"
        />
      )}

      {!events.data ? (
        <SurfaceError error={events.error ?? ""} status={events.status} what="the audit ledger" />
      ) : (
        <Card>
          <DataTable
            caption="Audit ledger entries"
            columns={[
              { key: "seq", label: "#", numeric: true },
              { key: "category", label: "Category" },
              { key: "action", label: "Action" },
              { key: "outcome", label: "Outcome" },
              { key: "resource", label: "Resource" },
              { key: "identities", label: "Identities" },
              { key: "hash", label: "Entry hash" },
              { key: "when", label: "When" },
            ]}
            rows={events.data.events.map((event) => ({
              __key: event.sequence_no,
              seq: event.sequence_no,
              category: <span className="mono">{event.category}</span>,
              action: <span className="mono">{event.action}</span>,
              outcome: <Status value={event.outcome} />,
              resource: (
                <span className="mono muted">
                  {event.resource_type}
                  {event.resource_id ? `:${event.resource_id.slice(0, 12)}` : ""}
                </span>
              ),
              identities: (
                <span className="mono muted" style={{ fontSize: 11 }}>
                  {[event.agent_id && `agent ${event.agent_id}`, event.tool_id && `tool ${event.tool_id}`]
                    .filter(Boolean)
                    .join(" · ") || "human"}
                </span>
              ),
              hash: <span className="mono muted">{event.entry_hash.slice(0, 12)}</span>,
              when: <span className="mono">{formatWhen(event.occurred_at)}</span>,
            }))}
          />
        </Card>
      )}
    </div>
  );
}