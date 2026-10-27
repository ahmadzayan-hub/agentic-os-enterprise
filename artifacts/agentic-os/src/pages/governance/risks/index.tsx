import { useApi } from "@/lib/use-api";
import { Card, DataTable, Status, SurfaceError, formatCost, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface RiskAssessment {
  action: string;
  risk_class: string;
  risk_score: number;
  factors: { name: string; weight: number; detail: string }[];
  reversibility: string;
  financial_impact_usd: number;
  required_autonomy: string;
  assessed_at: string;
}

export default function RisksPage() {
  const { data, error, status , loading } = useApi<{ risk_assessments: RiskAssessment[] }>(
    "/api/v1/risks?limit=100",
  );

  return (
    <div className="stack">
      <div>
        <h1>Risk</h1>
        <p className="page-lede">
          Risk is computed from observable factors, never asserted by the model that
          proposed the action. Each contribution is recorded, so a HIGH classification
          can be explained rather than trusted.
        </p>
      </div>
      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="risk assessments" />
      ) : (
        <Card>
          <DataTable
            caption="Risk assessments"
            empty="No risk assessment has been recorded."
            columns={[
              { key: "action", label: "Action" },
              { key: "class", label: "Class" },
              { key: "score", label: "Score", numeric: true },
              { key: "autonomy", label: "Requires" },
              { key: "reversibility", label: "Reversibility" },
              { key: "impact", label: "Impact", numeric: true },
              { key: "factors", label: "Contributing factors" },
              { key: "when", label: "When" },
            ]}
            rows={data.risk_assessments.map((assessment, index) => ({
              __key: index,
              action: <span className="mono">{assessment.action}</span>,
              class: <Status value={assessment.risk_class} />,
              score: assessment.risk_score.toFixed(3),
              autonomy: <span className="mono">{assessment.required_autonomy}</span>,
              reversibility: (
                <Status
                  value={assessment.reversibility}
                  tone={assessment.reversibility === "REVERSIBLE" ? "ok" : "warn"}
                />
              ),
              impact: formatCost(assessment.financial_impact_usd),
              factors: (
                <span className="mono muted" style={{ fontSize: 11 }}>
                  {(assessment.factors ?? [])
                    .map((factor) => `${factor.name}+${factor.weight}`)
                    .join(" ") || "—"}
                </span>
              ),
              when: <span className="mono">{formatWhen(assessment.assessed_at)}</span>,
            }))}
          />
        </Card>
      )}
    </div>
  );
}
