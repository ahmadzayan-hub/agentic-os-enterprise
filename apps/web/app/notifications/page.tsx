import Link from "next/link";

import { Card, Empty, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";
import type { NotificationItem } from "@/lib/types";

export const metadata = { title: "Inbox" };

/**
 * The inbox.
 *
 * Before this existed, a pending approval was discovered by opening the
 * console and looking — which is a habit, not a workflow. A notification is
 * created only for people who hold the permission the next step needs *and*
 * belong to the decision's domain, so the inbox never reveals that work exists
 * somewhere the reader cannot go.
 */
export default async function NotificationsPage() {
  const { data, error, status } = await apiTry<{ items: NotificationItem[]; unread: number }>(
    "/api/v1/notifications",
  );

  return (
    <div className="stack">
      <div>
        <h1>Inbox</h1>
        <p className="page-lede">
          Decisions that have reached a point where they need you. {data ? data.unread : 0}{" "}
          unread.
        </p>
      </div>

      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="your inbox" />
      ) : data.items.length === 0 ? (
        <Card>
          <Empty>
            Nothing needs you right now. Items appear here when a decision in one of
            your domains reaches a step you are entitled to take.
          </Empty>
        </Card>
      ) : (
        <div className="stack-sm">
          {data.items.map((item) => (
            <article className="card" key={item.id}>
              <div className="card-head">
                <div>
                  <div className="row" style={{ gap: 8, alignItems: "center" }}>
                    <span className="badge badge-muted">{item.kind.replace(/_/g, " ")}</span>
                    {item.decision_state ? <Status value={item.decision_state} /> : null}
                    {item.read_at ? null : <span className="badge badge-info">Unread</span>}
                  </div>
                  <h2 style={{ fontSize: 16, margin: "6px 0 0" }}>
                    {item.decision_id ? (
                      <Link href={`/decisions/${item.decision_id}`}>{item.subject}</Link>
                    ) : (
                      item.subject
                    )}
                  </h2>
                </div>
                <span className="muted" style={{ fontSize: 13 }}>
                  {formatWhen(item.created_at)}
                </span>
              </div>
              {item.body ? <p style={{ marginBlockEnd: 0 }}>{item.body}</p> : null}
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
