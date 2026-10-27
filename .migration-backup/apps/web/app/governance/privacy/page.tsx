import { Card, DataTable, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface DsarRow {
  id: string;
  request_type: string;
  subject_email: string;
  status: string;
  due_at: string | null;
  completed_at: string | null;
  affected_records: Record<string, unknown>;
  created_at: string;
}

interface HoldRow {
  hold_key: string;
  reason: string;
  resource_type: string;
  active: boolean;
  created_at: string;
  released_at: string | null;
}

interface ActivityRow {
  activity: string;
  purpose: string;
  legal_basis: string;
  data_categories: string[];
  recipients: string[];
  cross_border: boolean;
  retention: string;
  controller: string;
}

interface PiiRow {
  pii_type: string;
  occurrences: number;
  redacted: number;
}

function summarise(affected: Record<string, unknown> | null): string {
  if (!affected || Object.keys(affected).length === 0) return "—";
  return Object.entries(affected)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : String(value)}`)
    .join(" · ");
}

export default async function PrivacyPage() {
  const register = await apiTry<{
    requests: DsarRow[];
    legal_holds: HoldRow[];
    processing_activities: ActivityRow[];
    pii_summary: PiiRow[];
  }>("/api/v1/privacy");

  if (!register.data) {
    return (
      <div className="stack">
        <h1>Privacy</h1>
        <SurfaceError
          error={register.error ?? ""}
          status={register.status}
          what="the privacy register"
        />
      </div>
    );
  }

  const { requests, legal_holds, processing_activities, pii_summary } = register.data;

  return (
    <div className="stack">
      <div>
        <h1>Privacy</h1>
        <p className="page-lede">
          Data subject requests, the legal holds that override them, and the
          processing activities they are measured against. A deletion request
          that meets an active hold is parked rather than partly executed, and
          the audit ledger is never erased — identifiers in it are pseudonymised
          instead.
        </p>
      </div>

      <Card title="Data subject requests">
        <DataTable
          caption="Data subject requests"
          empty="No data subject request has been raised."
          columns={[
            { key: "type", label: "Type" },
            { key: "subject", label: "Subject" },
            { key: "status", label: "Status" },
            { key: "affected", label: "Records" },
            { key: "due", label: "Due" },
            { key: "completed", label: "Completed" },
          ]}
          rows={requests.map((request) => ({
            __key: request.id,
            type: <span className="mono">{request.request_type}</span>,
            subject: <span className="mono muted">{request.subject_email}</span>,
            status: (
              <Status
                value={request.status}
                tone={
                  request.status === "COMPLETED"
                    ? "ok"
                    : request.status === "BLOCKED_BY_HOLD"
                      ? "warn"
                      : undefined
                }
              />
            ),
            affected: <span className="muted">{summarise(request.affected_records)}</span>,
            due: <span className="mono">{formatWhen(request.due_at)}</span>,
            completed: <span className="mono">{formatWhen(request.completed_at)}</span>,
          }))}
        />
      </Card>

      <Card title="Legal holds">
        <DataTable
          caption="Legal holds"
          empty="No legal hold is recorded."
          columns={[
            { key: "hold", label: "Hold" },
            { key: "reason", label: "Reason" },
            { key: "resource", label: "Applies to" },
            { key: "state", label: "State" },
            { key: "raised", label: "Raised" },
          ]}
          rows={legal_holds.map((hold) => ({
            __key: hold.hold_key,
            hold: <span className="mono">{hold.hold_key}</span>,
            reason: <span className="muted">{hold.reason}</span>,
            resource: <span className="mono muted">{hold.resource_type}</span>,
            state: (
              <Status value={hold.active ? "ACTIVE" : "RELEASED"} tone={hold.active ? "warn" : "ok"} />
            ),
            raised: <span className="mono">{formatWhen(hold.created_at)}</span>,
          }))}
        />
      </Card>

      <Card title="Processing activities">
        <DataTable
          caption="Processing activities"
          empty="No processing activity is registered."
          columns={[
            { key: "activity", label: "Activity" },
            { key: "purpose", label: "Purpose" },
            { key: "basis", label: "Legal basis" },
            { key: "categories", label: "Data categories" },
            { key: "retention", label: "Retention" },
            { key: "border", label: "Cross border" },
          ]}
          rows={processing_activities.map((activity) => ({
            __key: activity.activity,
            activity: <span className="mono">{activity.activity}</span>,
            purpose: <span className="muted">{activity.purpose}</span>,
            basis: <span className="mono">{activity.legal_basis}</span>,
            categories: (
              <span className="muted">{(activity.data_categories ?? []).join(", ") || "—"}</span>
            ),
            retention: <span className="muted">{activity.retention || "—"}</span>,
            border: <Status value={activity.cross_border ? "YES" : "NO"} tone={activity.cross_border ? "warn" : "ok"} />,
          }))}
        />
      </Card>

      <Card title="Personal data detected in ingested content">
        <DataTable
          caption="Personal data detected in ingested content"
          empty="No personal data has been detected during ingestion."
          columns={[
            { key: "type", label: "Type" },
            { key: "occurrences", label: "Occurrences" },
            { key: "redacted", label: "Redacted" },
          ]}
          rows={pii_summary.map((entry) => ({
            __key: entry.pii_type,
            type: <span className="mono">{entry.pii_type}</span>,
            occurrences: <span className="mono">{entry.occurrences}</span>,
            redacted: <span className="mono">{entry.redacted}</span>,
          }))}
        />
      </Card>
    </div>
  );
}
