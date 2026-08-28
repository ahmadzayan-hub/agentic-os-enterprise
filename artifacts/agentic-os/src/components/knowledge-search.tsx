import { useState } from "react";
import { apiFetch, ApiError } from "@/lib/api";

interface Result {
  chunk_id: string;
  document_id: string;
  title: string;
  section: string;
  score: number;
  snippet: string;
  classification: string;
}

interface SearchResponse {
  results: Result[];
  candidates_before_acl: number;
  candidates_after_acl: number;
  acl_filtered_count: number;
  clearance_ceiling: string;
  latency_ms: number;
  message?: string;
}

export function KnowledgeSearch() {
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = await apiFetch<SearchResponse>("/api/v1/knowledge/search", {
        method: "POST",
        body: JSON.stringify({ query, top_k: 10, strategy: "hybrid" }),
      });
      setResponse(body);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Search failed.");
      setResponse(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <form onSubmit={submit} className="card">
        <div className="field">
          <label htmlFor="q">Search governed knowledge</label>
          <input
            id="q"
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="step chain elongation failure mode"
            minLength={2}
            required
            aria-describedby="q-hint"
          />
          <p className="field-hint" id="q-hint">
            Hybrid retrieval: dense vectors fused with full-text ranking. Access
            control is applied in the query, before ranking.
          </p>
        </div>
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "Searching…" : "Search"}
        </button>
      </form>

      {error ? (
        <div className="notice notice-danger" role="alert">
          {error}
        </div>
      ) : null}

      {response ? (
        <>
          <div className="notice" role="status" aria-live="polite">
            {response.results.length} result
            {response.results.length === 1 ? "" : "s"} in {response.latency_ms} ms ·
            clearance ceiling {response.clearance_ceiling} ·{" "}
            <strong>{response.acl_filtered_count}</strong> of{" "}
            {response.candidates_before_acl} indexed chunks are not visible to you.
          </div>

          {response.results.length === 0 ? (
            <p className="empty">
              Nothing you are authorised to read matches this query.
            </p>
          ) : (
            <div className="stack" style={{ gap: 10 }}>
              {response.results.map((result) => (
                <article className="card" key={result.chunk_id}>
                  <div className="row">
                    <strong>{result.title}</strong>
                    <span className={`badge badge-muted`}>{result.classification}</span>
                    <span className="mono muted">score {result.score.toFixed(4)}</span>
                    {result.section ? (
                      <span className="mono muted">{result.section}</span>
                    ) : null}
                  </div>
                  <p style={{ margin: "8px 0 0", fontSize: 13 }}>{result.snippet}</p>
                  <div className="mono muted" style={{ marginTop: 6, fontSize: 11 }}>
                    chunk {result.chunk_id.slice(0, 8)} · document{" "}
                    {result.document_id.slice(0, 8)}
                  </div>
                </article>
              ))}
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}