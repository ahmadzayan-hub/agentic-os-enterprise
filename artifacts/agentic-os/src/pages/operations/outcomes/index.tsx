import { useApi } from "@/lib/use-api";
import { Card, DataTable, Empty, Notice, Stat, SurfaceError, formatCost } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Roi {
  window_days: number;
  platform_cost_usd: number;
  measured_value_usd: number;
  net_value_usd: number;
  roi_ratio: number | null;
  measured: { outcome_type: string; quantity: number; value: number; records: number }[];
  estimated: { outcome_type: string; quantity: number; value: number; records: number }[];
  basis_note: string;
  monetisation_note: string;
}

export default function OutcomesPage() {
  const { data, error, status , loading } = useApi<Roi>("/api/v1/outcomes?window_days=30");
  if (loading) return <div className="empty">Loading...</div>;


  if (!data) {
    return (
      <>
        <h1>Business outcomes</h1>
        <SurfaceError error={error ?? ""} status={status} what="business outcomes" />
      </>
    );
  }

  return (
    <div className="stack">
      <div>
        <h1>Business outcomes</h1>
        <p className="page-lede">
          Value the platform can prove, separated from value someone estimated.
        </p>
      </div>

      <Notice tone="info">{data.basis_note}</Notice>

      <div className="grid grid-4">
        <Stat label="Measured value" value={formatCost(data.measured_value_usd)} note="evidence-backed only" />
        <Stat label="Platform cost" value={formatCost(data.platform_cost_usd)} />
        <Stat label="Net" value={formatCost(data.net_value_usd)} />
        <Stat
          label="ROI"
          value={data.roi_ratio === null ? "—" : `${data.roi_ratio.toFixed(2)}×`}
          note={data.roi_ratio === null ? "no platform cost recorded yet" : undefined}
        />
      </div>

      <div className="grid grid-2">
        <Card title="Measured outcomes">
          {data.measured.length === 0 ? (
            <Empty>
              No measured outcome recorded. A measured outcome must carry evidence
              references, which is why this list is empty rather than optimistic.
            </Empty>
          ) : (
            <DataTable
              caption="Measured outcomes"
              columns={[
                { key: "type", label: "Outcome" },
                { key: "quantity", label: "Quantity", numeric: true },
                { key: "value", label: "Value", numeric: true },
                { key: "records", label: "Records", numeric: true },
              ]}
              rows={data.measured.map((row) => ({
                __key: row.outcome_type,
                type: <span className="mono">{row.outcome_type}</span>,
                quantity: Number(row.quantity).toFixed(2),
                value: formatCost(row.value),
                records: row.records,
              }))}
            />
          )}
        </Card>

        <Card title="Estimated outcomes — excluded from ROI">
          {data.estimated.length === 0 ? (
            <Empty>No estimated outcome recorded.</Empty>
          ) : (
            <DataTable
              caption="Estimated outcomes"
              columns={[
                { key: "type", label: "Outcome" },
                { key: "quantity", label: "Quantity", numeric: true },
                { key: "value", label: "Stated value", numeric: true },
              ]}
              rows={data.estimated.map((row) => ({
                __key: row.outcome_type,
                type: <span className="mono">{row.outcome_type}</span>,
                quantity: Number(row.quantity).toFixed(2),
                value: formatCost(row.value),
              }))}
            />
          )}
        </Card>
      </div>

      <Card title="On monetisation">
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          {data.monetisation_note}
        </p>
      </Card>
    </div>
  );
}
