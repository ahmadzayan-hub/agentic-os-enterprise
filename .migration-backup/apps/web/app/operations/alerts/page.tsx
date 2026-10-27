import {
  Card,
  DataTable,
  Empty,
  Notice,
  Stat,
  Status,
  SurfaceError,
  formatWhen,
} from "@/components/ui";
import { apiTry } from "@/lib/api";
import {
  alertOwnerLabel,
  alertSummary,
  escalationLabel,
  occurrenceLabel,
} from "@/lib/display";
import type { AlertList } from "@/lib/types";

export const metadata = { title: "Alerts" };

/**
 * The alert list.
 *
 * The `alerts` table shipped in 2026 and never held a row: nothing raised one,
 * and the only statement anywhere that touched it was a read. So this surface
 * is not a nicer view of an existing capability — it is the first place the
 * platform tells somebody, unprompted, that something is wrong.
 *
 * Three things it deliberately refuses to do:
 *
 * It does not default to open alerts only. A list that hides resolved alerts
 * makes "we had no incidents" and "we resolved four" look identical, and the
 * second is the answer worth having.
 *
 * It does not leave an unassigned alert blank. The platform could find nobody
 * holding the required permission inside the relevant domain, and that is a
 * staffing fact, not a rendering gap.
 *
 * It does not show a count without saying what it counts. Every figure here is
 * computed under the same permission and domain boundary as the list beneath
 * it, so the two can never disagree — a total taken outside that boundary
 * would report how many alerts exist that the reader may not see.
 */
export default async function AlertsPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; acknowledged?: string; status?: string }>;
}) {
  const params = await searchParams;
  const query = params.status ? `?status=${encodeURIComponent(params.status)}` : "";
  const { data, error, status } = await apiTry<AlertList>(`/api/v1/alerts${query}`);

  return (
    <div className="stack">
      <div>
        <h1>Alerts</h1>
        <p className="page-lede">
          Conditions the platform noticed on its own, routed to somebody who holds
          the permission to act on them and belongs to the domain they concern.
          Alerts you cannot act on are not listed.
        </p>
      </div>

      {params.error ? <Notice tone="danger">{params.error}</Notice> : null}
      {params.acknowledged ? (
        <Notice tone="info">
          Acknowledged. Your name is now recorded against it and it will stop
          escalating.
        </Notice>
      ) : null}

      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="alerts" />
      ) : (
        <>
          <Card title="Where things stand">
            <p className="page-lede" style={{ marginTop: 0 }}>
              {alertSummary(data.counts)}
            </p>
            <div className="grid grid-4">
              <Stat label="Open" value={String(data.counts.open)} />
              <Stat label="Open and critical" value={String(data.counts.critical_open)} />
              <Stat label="Nobody assigned" value={String(data.counts.unassigned)} />
              <Stat label="Raised in total" value={String(data.counts.total)} />
            </div>
            <p className="muted" style={{ fontSize: 13 }}>
              These figures are computed under the same permission and domain
              boundary as the list below, so they never describe an alert this
              page is not showing you.
            </p>
          </Card>

          {data.alerts.length === 0 ? (
            <Card>
              <Empty>
                No alert has been raised in your domains. Alerts appear here when an
                evaluation pass finds a condition true — a run failing repeatedly, a
                KPI past its warning threshold, a decision overdue, or the audit
                ledger&rsquo;s hash chain failing to verify.
              </Empty>
            </Card>
          ) : (
            <Card title="Alerts">
              <DataTable
                caption="Alerts raised in your domains"
                empty="No alert has been raised."
                columns={[
                  { key: "title", label: "What is wrong" },
                  { key: "severity", label: "Severity" },
                  { key: "state", label: "State" },
                  { key: "owner", label: "Assigned to" },
                  { key: "seen", label: "Recurrence" },
                  { key: "raised", label: "Raised" },
                  { key: "act", label: "Action", hideLabel: true },
                ]}
                rows={data.alerts.map((alert) => ({
                  __key: alert.id,
                  title: (
                    <div>
                      <div>{alert.title}</div>
                      <span className="muted" style={{ fontSize: 12 }}>
                        {alert.domain_name ?? "Across all domains"} ·{" "}
                        <span className="mono">{alert.alert_type}</span>
                      </span>
                    </div>
                  ),
                  severity: <Status value={alert.severity} />,
                  state: (
                    <div>
                      <Status value={alert.status} />
                      <div className="muted" style={{ fontSize: 12 }}>
                        {escalationLabel(alert.escalation_level)}
                      </div>
                    </div>
                  ),
                  owner: (
                    <span className={alert.assigned_to_email ? undefined : "muted"}>
                      {alertOwnerLabel(alert.assigned_to_email)}
                    </span>
                  ),
                  seen: (
                    <div>
                      <div>{occurrenceLabel(alert.occurrence_count)}</div>
                      <span className="muted mono" style={{ fontSize: 12 }}>
                        last {formatWhen(alert.last_seen_at)}
                      </span>
                    </div>
                  ),
                  raised: <span className="mono">{formatWhen(alert.created_at)}</span>,
                  act:
                    alert.status === "OPEN" ? (
                      <form
                        method="post"
                        action={`/api/alerts/${alert.id}/acknowledge`}
                      >
                        <button className="btn" type="submit">
                          Acknowledge
                        </button>
                      </form>
                    ) : alert.acknowledged_by_email ? (
                      <span className="muted" style={{ fontSize: 12 }}>
                        {alert.acknowledged_by_email}
                      </span>
                    ) : (
                      <span className="muted" style={{ fontSize: 12 }}>
                        —
                      </span>
                    ),
                }))}
              />
            </Card>
          )}

          <Card title="How an alert reaches you">
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
              An evaluation pass runs each registered rule and reconciles the list:
              a condition that is still true updates the alert already raised for
              it rather than raising another, and a condition that has cleared
              resolves its own alert. A rule that fails to run is recorded as
              failed and does <strong>not</strong> resolve the alerts it raised
              earlier — treating a broken check as good news is the one direction
              that error must never take.
            </p>
            <p className="muted" style={{ fontSize: 13 }}>
              Assignment requires both the permission the alert names and
              membership of its domain. Where the platform can find nobody who
              holds both, the alert stays visibly unassigned rather than being
              given to somebody who cannot act on it.
            </p>
          </Card>
        </>
      )}
    </div>
  );
}
