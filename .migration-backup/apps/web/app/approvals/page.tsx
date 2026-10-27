import { Card, Empty, Notice, Status, SurfaceError, formatCost, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";
import type { ApprovalCard } from "@/lib/types";

/**
 * The approval card is the human's decision surface. It carries everything the
 * brief requires — proposing agent, action, target, financial impact,
 * confidence, reason, evidence, sources, risk, reversibility, governing policy,
 * consequences and autonomy level — so nobody has to leave the page to decide.
 */
export default async function ApprovalsPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; decided?: string }>;
}) {
  const params = await searchParams;
  const { data, error, status } = await apiTry<{ approvals: ApprovalCard[] }>(
    "/api/v1/approvals?mine=true",
  );

  return (
    <div className="stack">
      <div>
        <h1>Approvals</h1>
        <p className="page-lede">
          Consequential actions a machine proposed and a named human must decide.
          Nothing here has executed.
        </p>
      </div>

      {params.decided ? <Notice tone="info">Decision recorded.</Notice> : null}
      {params.error ? <Notice tone="danger">{params.error}</Notice> : null}

      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="approvals" />
      ) : data.approvals.length === 0 ? (
        <Card>
          <Empty>
            Nothing is waiting for your authorisation. Approvals appear here when an
            agent proposes a consequential action you are entitled to decide.
          </Empty>
        </Card>
      ) : (
        <div className="stack">
          {data.approvals.map((approval) => (
            <article className="card" key={approval.id} id={approval.id}>
              <div className="card-head">
                <div>
                  <h2 style={{ marginBottom: 4 }}>{approval.action}</h2>
                  {approval.target ? (
                    <div className="muted" style={{ fontSize: 13 }}>
                      {approval.target}
                    </div>
                  ) : null}
                </div>
                <div className="row">
                  <Status value={approval.risk_class} />
                  <span className="badge badge-muted">{approval.autonomy_level}</span>
                  <span className="badge badge-muted">{approval.mode}</span>
                </div>
              </div>

              <dl className="grid grid-4" style={{ margin: "0 0 14px" }}>
                <div>
                  <dt className="stat-label">Proposing agent</dt>
                  <dd className="mono" style={{ margin: 0 }}>
                    {approval.requested_by_agent || "—"}
                  </dd>
                </div>
                <div>
                  <dt className="stat-label">Financial impact</dt>
                  <dd className="mono" style={{ margin: 0 }}>
                    {formatCost(approval.financial_impact_usd)}
                  </dd>
                </div>
                <div>
                  <dt className="stat-label">Reversibility</dt>
                  <dd style={{ margin: 0 }}>
                    <Status
                      value={approval.reversibility}
                      tone={approval.reversibility === "REVERSIBLE" ? "ok" : "danger"}
                    />
                  </dd>
                </div>
                <div>
                  <dt className="stat-label">Confidence</dt>
                  <dd className="mono" style={{ margin: 0 }}>
                    {approval.confidence === null
                      ? "not stated"
                      : `${(approval.confidence * 100).toFixed(0)}%`}
                  </dd>
                </div>
              </dl>

              <div className="grid grid-2" style={{ marginBottom: 14 }}>
                <div>
                  <h3>Why this was proposed</h3>
                  <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                    {approval.reason}
                  </p>
                </div>
                <div>
                  <h3>What follows if you approve</h3>
                  <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                    {approval.consequences}
                  </p>
                </div>
              </div>

              {approval.policy_refs.length > 0 ? (
                <div style={{ marginBottom: 14 }}>
                  <h3>Governing policy</h3>
                  <ul className="mono muted" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                    {approval.policy_refs.map((ref, index) => {
                      const entry = ref as { policy?: string; rule?: string; effect?: string };
                      return (
                        <li key={index}>
                          {entry.policy}/{entry.rule} → {entry.effect}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ) : null}

              {approval.evidence.length > 0 ? (
                <details style={{ marginBottom: 14 }}>
                  <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 13 }}>
                    Evidence ({approval.evidence.length})
                  </summary>
                  <pre className="code" style={{ marginTop: 8 }}>
                    {JSON.stringify(approval.evidence, null, 2)}
                  </pre>
                </details>
              ) : null}

              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="mono muted">
                  needs {approval.required_approvals} approval
                  {approval.required_approvals === 1 ? "" : "s"} · expires{" "}
                  {formatWhen(approval.expires_at)}
                </span>
                <form action={`/api/approvals/${approval.id}`} method="post" className="row">
                  <label htmlFor={`comment-${approval.id}`} className="visually-hidden">
                    Decision comment
                  </label>
                  <input
                    id={`comment-${approval.id}`}
                    name="comment"
                    type="text"
                    placeholder="Reason for your decision"
                    style={{ width: 260 }}
                  />
                  <button
                    className="btn"
                    type="submit"
                    name="decision"
                    value="CHANGES_REQUESTED"
                  >
                    Request changes
                  </button>
                  <button className="btn btn-danger" type="submit" name="decision" value="REJECTED">
                    Reject
                  </button>
                  <button
                    className="btn btn-primary"
                    type="submit"
                    name="decision"
                    value="APPROVED"
                  >
                    Approve
                  </button>
                </form>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
