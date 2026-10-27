import { useState } from "react";
import { useApi } from "@/lib/use-api";
import { Card, DataTable, Notice, Status, SurfaceError, formatWhen } from "@/components/ui";
import { apiFetch } from "@/lib/api";

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
  const { data, error, status, refetch } = useApi<{ documents: Document[] }>(
    "/api/v1/documents",
  );
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function ingest(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setMessage(null);
    const form = new FormData(event.currentTarget);
    try {
      await apiFetch("/api/v1/documents", {
        method: "POST",
        body: JSON.stringify({
          title: form.get("title"),
          content: form.get("content"),
          classification: form.get("classification"),
        }),
      });
      event.currentTarget.reset();
      setMessage("Document ingested and added to governed knowledge.");
      refetch();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Ingestion failed.");
    } finally {
      setSubmitting(false);
    }
  }

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
      <Card title="Ingest a document">
        <form onSubmit={ingest} className="stack">
          <div className="grid grid-2">
            <div className="field">
              <label htmlFor="document-title">Title</label>
              <input id="document-title" name="title" required minLength={3} />
            </div>
            <div className="field">
              <label htmlFor="document-classification">Classification</label>
              <select id="document-classification" name="classification" defaultValue="INTERNAL">
                <option>PUBLIC</option><option>INTERNAL</option><option>CONFIDENTIAL</option>
              </select>
            </div>
          </div>
          <div className="field">
            <label htmlFor="document-content">Document text</label>
            <textarea id="document-content" name="content" required minLength={10} rows={5} />
            <p className="field-hint">Text is scanned, classified, chunked, and persisted for governed retrieval.</p>
          </div>
          <button className="btn btn-primary" disabled={submitting}>
            {submitting ? "Ingesting…" : "Ingest document"}
          </button>
        </form>
        {message ? <Notice tone={message.includes("failed") ? "danger" : "info"}>{message}</Notice> : null}
      </Card>
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
