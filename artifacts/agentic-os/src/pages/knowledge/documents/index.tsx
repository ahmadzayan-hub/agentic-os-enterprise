import { useApi } from "@/lib/use-api";
import { Card, DataTable, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiTry } from "@/lib/api";

interface Document {
  id: string;
  title: string;
  source_system: string;
  mime_type: string;
  byte_size: number;
  classification: string;
  owner_team: string;
  ingest_status: string;
  malware_scan_status: string;
  dlp_labels: string[];
  parse_confidence: number | null;
  unsupported_elements: string[];
  chunk_count: number;
  created_at: string;
}

export default function DocumentsPage() {
  const { data, error, status , loading } = useApi<{ documents: Document[] }>(
    "/api/v1/documents",
  );

  return (
    <div className="stack">
      <div>
        <h1>Documents</h1>
        <p className="page-lede">
          Every document passed a ten-stage governed pipeline. Parse confidence and
          unsupported elements are reported rather than hidden, so an answer drawn
          from a partially extracted source can be qualified.
        </p>
      </div>
      {!data ? (
        <SurfaceError error={error ?? ""} status={status} what="documents" />
      ) : (
        <Card>
          <DataTable
            caption="Governed documents"
            empty="No document is visible to you."
            columns={[
              { key: "title", label: "Document" },
              { key: "classification", label: "Classification" },
              { key: "ingest", label: "Ingest" },
              { key: "scan", label: "Scan" },
              { key: "pii", label: "PII labels" },
              { key: "confidence", label: "Parse confidence", numeric: true },
              { key: "chunks", label: "Chunks", numeric: true },
              { key: "created", label: "Ingested" },
            ]}
            rows={data.documents.map((document) => ({
              __key: document.id,
              title: (
                <>
                  <strong>{document.title}</strong>
                  <div className="mono muted">
                    {document.source_system} · {document.mime_type}
                  </div>
                  {document.unsupported_elements?.length ? (
                    <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>
                      not extracted: {document.unsupported_elements.join("; ")}
                    </div>
                  ) : null}
                </>
              ),
              classification: <Status value={document.classification} />,
              ingest: <Status value={document.ingest_status} />,
              scan: <Status value={document.malware_scan_status} />,
              pii: document.dlp_labels?.length ? (
                <span className="mono muted">{document.dlp_labels.join(", ")}</span>
              ) : (
                <span className="muted">none detected</span>
              ),
              confidence:
                document.parse_confidence === null
                  ? "—"
                  : `${(document.parse_confidence * 100).toFixed(0)}%`,
              chunks: document.chunk_count,
              created: <span className="mono">{formatWhen(document.created_at)}</span>,
            }))}
          />
        </Card>
      )}
    </div>
  );
}
