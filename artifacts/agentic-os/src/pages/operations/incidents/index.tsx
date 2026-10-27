import { useApi } from "@/lib/use-api";
import { Card, DataTable, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Incidents {
  incidents: {
    incident_key: string;
    title: string;
    description: string;
    severity: string;
    status: string;
    category: string;
    root_cause: string;
    detected_at: string;
    resolved_at: string | null;
  }[];
  alerts: {
    alert_type: string;
    severity: string;
    title: string;
    source: string;
    acknowledged_at: string | null;
    created_at: string;
  }[];
}

export default function IncidentsPage() {
  const { data, error, status , loading } = useApi<Incidents>("/api/v1/incidents");
  if (loading) return <div className="empty">Loading...</div>;


  return (
    <div className="stack">
      <div>
        <h1>Incidents and alerts</h1>
        <p className="page-lede">
          Operational events raised by the platform or by an operator, with the
          resolution state that governs whether a run may proceed.
        </p>
      </div>
      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="incidents" />
      ) : (
        <>
          <Card title="Incidents">
            <DataTable
              caption="Incidents"
              empty="No incident has been raised."
              columns={[
                { key: "key", label: "Key" },
                { key: "title", label: "Title" },
                { key: "severity", label: "Severity" },
                { key: "status", label: "Status" },
                { key: "detected", label: "Detected" },
              ]}
              rows={data.incidents.map((incident) => ({
                __key: incident.incident_key,
                key: <span className="mono">{incident.incident_key}</span>,
                title: incident.title,
                severity: <Status value={incident.severity} />,
                status: <Status value={incident.status} />,
                detected: <span className="mono">{formatWhen(incident.detected_at)}</span>,
              }))}
            />
          </Card>
          <Card title="Alerts">
            <DataTable
              caption="Alerts"
              empty="No alert has been raised."
              columns={[
                { key: "type", label: "Type" },
                { key: "title", label: "Title" },
                { key: "severity", label: "Severity" },
                { key: "ack", label: "Acknowledged" },
                { key: "created", label: "Raised" },
              ]}
              rows={data.alerts.map((alert, index) => ({
                __key: index,
                type: <span className="mono">{alert.alert_type}</span>,
                title: alert.title,
                severity: <Status value={alert.severity} />,
                ack: alert.acknowledged_at ? (
                  <span className="badge badge-ok">acknowledged</span>
                ) : (
                  <span className="badge badge-warn">open</span>
                ),
                created: <span className="mono">{formatWhen(alert.created_at)}</span>,
              }))}
            />
          </Card>
        </>
      )}
    </div>
  );
}
