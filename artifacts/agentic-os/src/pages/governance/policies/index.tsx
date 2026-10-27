import { useApi } from "@/lib/use-api";
import { Card, DataTable, Status, SurfaceError, formatWhen } from "@/components/ui";

interface Policy {
  policy_key: string;
  name: string;
  description: string;
  category: string;
  owner_team: string;
  enforcement: string;
  status: string;
  current_version: number;
  rules: { name: string; effect: string; reason: string }[];
  rules_hash: string;
}

interface Decision {
  action: string;
  resource: string;
  effect: string;
  reason: string;
  evaluated_at: string;
}

export default function PoliciesPage() {
  const policies = useApi<{ policies: Policy[] }>("/api/v1/policies");
  const decisions = useApi<{ decisions: Decision[] }>("/api/v1/policies/decisions?limit=50");

  const loading = policies.loading || decisions.loading;
  if (loading) return <div className="empty">Loading...</div>;

  return (
    <div className="stack">
      <div>
        <h1>Policies</h1>
        <p className="page-lede">
          Deny overrides, with an explicit default of deny: an action no policy
          contemplates is not permitted. A policy in MONITOR mode records its
          decision without blocking, which is how a new policy is rolled out.
        </p>
      </div>

      {!policies.data ? (
        <SurfaceError error={policies.error ?? ""} status={policies.status} what="policies" />
      ) : (
        <div className="stack">
          {policies.data.policies.map((policy) => (
            <Card
              key={policy.policy_key}
              title={policy.name}
              action={
                <div className="row">
                  <Status
                    value={policy.enforcement}
                    tone={policy.enforcement === "ENFORCE" ? "ok" : "warn"}
                  />
                  <span className="mono muted">
                    {policy.policy_key} v{policy.current_version} ·{" "}
                    {policy.rules_hash?.slice(0, 10)}
                  </span>
                </div>
              }
            >
              <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
                {policy.description}
              </p>
              <DataTable
                caption={`Rules in ${policy.policy_key}`}
                columns={[
                  { key: "rule", label: "Rule" },
                  { key: "effect", label: "Effect" },
                  { key: "reason", label: "Reason" },
                ]}
                rows={(policy.rules ?? []).map((rule) => ({
                  __key: rule.name,
                  rule: <span className="mono">{rule.name}</span>,
                  effect: <Status value={rule.effect} />,
                  reason: <span className="muted">{rule.reason}</span>,
                }))}
              />
            </Card>
          ))}
        </div>
      )}

      <Card title="Recent policy decisions">
        {!decisions.data ? (
          <SurfaceError
            error={decisions.error ?? ""}
            status={decisions.status}
            what="policy decisions"
          />
        ) : (
          <DataTable
            caption="Recent policy decisions"
            empty="No policy decision has been evaluated."
            columns={[
              { key: "action", label: "Action" },
              { key: "resource", label: "Resource" },
              { key: "effect", label: "Effect" },
              { key: "reason", label: "Reason" },
              { key: "when", label: "When" },
            ]}
            rows={decisions.data.decisions.map((decision, index) => ({
              __key: index,
              action: <span className="mono">{decision.action}</span>,
              resource: <span className="mono muted">{decision.resource}</span>,
              effect: <Status value={decision.effect} />,
              reason: <span className="muted">{decision.reason}</span>,
              when: <span className="mono">{formatWhen(decision.evaluated_at)}</span>,
            }))}
          />
        )}
      </Card>
    </div>
  );
}