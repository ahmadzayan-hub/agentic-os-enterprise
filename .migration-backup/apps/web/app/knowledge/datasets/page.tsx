import { Card, DataTable, Meter, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Dataset {
  dataset_key: string;
  name: string;
  description: string;
  source_system: string;
  owner_team: string;
  classification: string;
  row_count: number;
  quality_score: number | null;
  quality_detail: { dimensions?: Record<string, number> };
  freshness_at: string | null;
  primary_key_field: string;
}

export default async function DatasetsPage() {
  const { data, error, status } = await apiTry<{ datasets: Dataset[] }>(
    "/api/v1/datasets",
  );

  return (
    <div className="stack">
      <div>
        <h1>Datasets</h1>
        <p className="page-lede">
          Structured exports held as governed datasets with row-level lineage back to
          the source file and batch, so a tool can answer deterministically instead of
          asking a model to read a spreadsheet.
        </p>
      </div>
      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="datasets" />
      ) : (
        <Card>
          <DataTable
            caption="Governed datasets"
            empty="No dataset has been ingested."
            columns={[
              { key: "dataset", label: "Dataset" },
              { key: "source", label: "Source" },
              { key: "classification", label: "Classification" },
              { key: "rows", label: "Rows", numeric: true },
              { key: "quality", label: "Quality" },
              { key: "freshness", label: "Freshness" },
            ]}
            rows={data.datasets.map((dataset) => ({
              __key: dataset.dataset_key,
              dataset: (
                <>
                  <strong className="mono">{dataset.dataset_key}</strong>
                  <div className="muted" style={{ fontSize: 12 }}>{dataset.description}</div>
                </>
              ),
              source: <span className="mono">{dataset.source_system}</span>,
              classification: <Status value={dataset.classification} />,
              rows: dataset.row_count.toLocaleString(),
              quality:
                dataset.quality_score === null ? (
                  <span className="muted">not profiled</span>
                ) : (
                  <div style={{ minWidth: 120 }}>
                    <div className="mono" style={{ marginBottom: 4 }}>
                      {(dataset.quality_score * 100).toFixed(1)}%
                    </div>
                    <Meter
                      value={dataset.quality_score * 100}
                      label={`${dataset.dataset_key} data quality`}
                    />
                  </div>
                ),
              freshness: <span className="mono">{formatWhen(dataset.freshness_at)}</span>,
            }))}
          />
        </Card>
      )}
    </div>
  );
}
