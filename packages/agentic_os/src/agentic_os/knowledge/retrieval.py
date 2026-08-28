"""Governed retrieval.

The single most important property of this module: **authorization happens in
the SQL predicate, before ranking**. Unauthorised chunks are never fetched,
never scored and never reach a reranker — so there is no window in which they
exist in memory and could leak through a timing signal, a debug log or a
partially-applied filter.

Retrieval is hybrid by default: dense vector similarity (pgvector, cosine) is
fused with lexical relevance (PostgreSQL full-text ranking) using reciprocal
rank fusion, which needs no score calibration between the two systems.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import (
    DataClassification,
    ExecutionContext,
    as_classification,
    classification_rank,
)
from agentic_os.core.errors import AuthorizationError
from agentic_os.knowledge.embeddings import get_embedder

Strategy = Literal["hybrid", "semantic", "lexical"]

#: Reciprocal rank fusion constant. 60 is the value from the original RRF paper
#: and is insensitive to tuning.
RRF_K = 60


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_title: str
    content: str
    score: float
    classification: str
    section_path: str = ""
    page_from: int | None = None
    semantic_rank: int | None = None
    lexical_rank: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def snippet(self, limit: int = 320) -> str:
        return self.content[:limit] + ("..." if len(self.content) > limit else "")

    def citation(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "title": self.document_title,
            "section": self.section_path,
            "page": self.page_from,
            "score": round(self.score, 4),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.citation(),
            "snippet": self.snippet(),
            "classification": self.classification,
            "semantic_rank": self.semantic_rank,
            "lexical_rank": self.lexical_rank,
        }


@dataclass(slots=True)
class RetrievalResult:
    query: str
    strategy: str
    chunks: list[RetrievedChunk]
    candidates_before_acl: int
    candidates_after_acl: int
    latency_ms: int
    clearance_ceiling: str

    @property
    def acl_filtered_count(self) -> int:
        """Searchable chunks withheld from this caller by ACL and clearance."""
        return self.candidates_before_acl - self.candidates_after_acl

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "strategy": self.strategy,
            "returned": len(self.chunks),
            "candidates_before_acl": self.candidates_before_acl,
            "candidates_after_acl": self.candidates_after_acl,
            "acl_filtered_count": self.acl_filtered_count,
            "latency_ms": self.latency_ms,
            "clearance_ceiling": self.clearance_ceiling,
            "results": [c.to_dict() for c in self.chunks],
        }


def principal_acl_keys(ctx: ExecutionContext) -> list[str]:
    """Every ACL principal identifier this context may match."""
    keys = ["PUBLIC"]
    if ctx.human is not None:
        keys.append(f"USER:{ctx.human.user_id}")
        keys.extend(f"GROUP:{g}" for g in sorted(ctx.human.groups))
        keys.extend(f"ROLE:{r}" for r in sorted(ctx.human.roles))
    if ctx.agent is not None:
        keys.append(f"AGENT:{ctx.agent.agent_id}")
    return keys


def effective_clearance(ctx: ExecutionContext, agent_ceiling: str | None = None) -> DataClassification:
    """The lower of the human's clearance and the acting agent's ceiling.

    An agent can never see more than the person it is acting for, and a person
    can never see more through an agent than the agent's contract allows.

    The ceiling arrives as an untrusted string — it reaches this function from a
    tool parameter. The gateway validates it against the tool's enum before
    dispatch, so a bad value does not get here today, but the arithmetic below
    would be unforgiving if one ever did: `classification_rank` ranks an unknown
    value *above* RESTRICTED, and with no human in context that ceiling would be
    the only candidate and would rank higher than every document. So an
    unrecognised ceiling is clamped to PUBLIC here — the most restrictive
    answer — and the guarantee stops depending on a YAML enum staying correct.
    """
    candidates: list[DataClassification] = []
    if ctx.human is not None:
        candidates.append(ctx.human.clearance)
    if agent_ceiling:
        candidates.append(as_classification(agent_ceiling))
    if not candidates:
        return "PUBLIC"
    return min(candidates, key=classification_rank)


#: The authorization predicate. Applied inside every retrieval query.
_ACL_PREDICATE = """
    c.tenant_id = :tenant
    AND d.ingest_status = 'PUBLISHED'
    AND d.deleted_at IS NULL
    AND c.acl_principals && CAST(:acl_keys AS text[])
    AND CASE c.classification
          WHEN 'PUBLIC' THEN 0 WHEN 'INTERNAL' THEN 1
          WHEN 'CONFIDENTIAL' THEN 2 ELSE 3 END <= :clearance_rank
"""


def _semantic_candidates(
    session: Session,
    *,
    tenant_id: str,
    acl_keys: list[str],
    clearance_rank: int,
    embedding: list[float],
    limit: int,
    filters: dict[str, Any],
) -> list[dict]:
    extra, params = _extra_filters(filters)
    rows = (
        session.execute(
            text(
                f"""
            SELECT c.id AS chunk_id, c.document_id, d.title, c.content, c.classification,
                   c.section_path, c.page_from, c.metadata,
                   1 - (e.embedding <=> CAST(:query_vec AS vector)) AS similarity
            FROM chunks c
            JOIN documents d ON d.id = c.document_id AND d.tenant_id = c.tenant_id
            JOIN embeddings e ON e.chunk_id = c.id AND e.tenant_id = c.tenant_id
            WHERE {_ACL_PREDICATE} {extra}
            ORDER BY e.embedding <=> CAST(:query_vec AS vector)
            LIMIT :limit
            """
            ),
            {
                "tenant": tenant_id,
                "acl_keys": acl_keys,
                "clearance_rank": clearance_rank,
                "query_vec": str(embedding),
                "limit": limit,
                **params,
            },
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def _lexical_candidates(
    session: Session,
    *,
    tenant_id: str,
    acl_keys: list[str],
    clearance_rank: int,
    query: str,
    limit: int,
    filters: dict[str, Any],
) -> list[dict]:
    extra, params = _extra_filters(filters)
    rows = (
        session.execute(
            text(
                f"""
            SELECT c.id AS chunk_id, c.document_id, d.title, c.content, c.classification,
                   c.section_path, c.page_from, c.metadata,
                   ts_rank_cd(to_tsvector('english', c.content),
                              websearch_to_tsquery('english', :query)) AS similarity
            FROM chunks c
            JOIN documents d ON d.id = c.document_id AND d.tenant_id = c.tenant_id
            WHERE {_ACL_PREDICATE} {extra}
              AND to_tsvector('english', c.content) @@ websearch_to_tsquery('english', :query)
            ORDER BY similarity DESC
            LIMIT :limit
            """
            ),
            {
                "tenant": tenant_id,
                "acl_keys": acl_keys,
                "clearance_rank": clearance_rank,
                "query": query,
                "limit": limit,
                **params,
            },
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def _extra_filters(filters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Build additional metadata predicates. Only allowlisted keys are honoured."""
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if document_ids := filters.get("document_ids"):
        clauses.append("AND c.document_id = ANY(CAST(:f_doc_ids AS uuid[]))")
        params["f_doc_ids"] = list(document_ids)
    if source_system := filters.get("source_system"):
        clauses.append("AND d.source_system = :f_source")
        params["f_source"] = source_system
    if owner_team := filters.get("owner_team"):
        clauses.append("AND d.owner_team = :f_team")
        params["f_team"] = owner_team
    if language := filters.get("language"):
        clauses.append("AND d.language = :f_lang")
        params["f_lang"] = language
    return " ".join(clauses), params


def _corpus_visibility(
    session: Session,
    tenant_id: str,
    acl_keys: list[str],
    clearance_rank: int,
    filters: dict[str, Any],
) -> tuple[int, int]:
    """Return (searchable chunks in the tenant, chunks visible to this caller).

    Both figures are counts of the *searchable corpus*, not of the candidates a
    particular query happened to match — semantic search scans the whole index,
    so a query-scoped "before ACL" figure would compare a lexical subset against
    a hybrid result and be meaningless.

    Counting is safe: it never returns content, so the reported delta discloses
    only how much the ACL removed, not what.
    """
    extra, params = _extra_filters(filters)
    row = session.execute(
        text(
            f"""
            SELECT
              count(*) AS total,
              count(*) FILTER (
                WHERE c.acl_principals && CAST(:acl_keys AS text[])
                  AND CASE c.classification
                        WHEN 'PUBLIC' THEN 0 WHEN 'INTERNAL' THEN 1
                        WHEN 'CONFIDENTIAL' THEN 2 ELSE 3 END <= :clearance_rank
              ) AS visible
            FROM chunks c
            JOIN documents d ON d.id = c.document_id AND d.tenant_id = c.tenant_id
            WHERE c.tenant_id = :tenant AND d.ingest_status = 'PUBLISHED'
              AND d.deleted_at IS NULL {extra}
            """
        ),
        {
            "tenant": tenant_id,
            "acl_keys": acl_keys,
            "clearance_rank": clearance_rank,
            **params,
        },
    ).one()
    return int(row.total), int(row.visible)


def search(
    session: Session,
    ctx: ExecutionContext,
    query: str,
    *,
    top_k: int = 8,
    strategy: Strategy = "hybrid",
    filters: dict[str, Any] | None = None,
    agent_clearance_ceiling: str | None = None,
    candidate_multiplier: int = 4,
    record_query: bool = True,
) -> RetrievalResult:
    """ACL-aware hybrid retrieval. Raises if the caller cannot retrieve at all."""
    if ctx.human is None and not ctx.service_principal:
        raise AuthorizationError("retrieval requires an authenticated principal")

    filters = filters or {}
    started = time.perf_counter()
    acl_keys = principal_acl_keys(ctx)
    clearance = effective_clearance(ctx, agent_clearance_ceiling)
    clearance_rank = classification_rank(clearance)
    fetch = max(top_k * candidate_multiplier, top_k)

    semantic: list[dict] = []
    lexical: list[dict] = []

    if strategy in ("hybrid", "semantic"):
        embedding = get_embedder().embed([query])[0]
        semantic = _semantic_candidates(
            session,
            tenant_id=ctx.tenant_id,
            acl_keys=acl_keys,
            clearance_rank=clearance_rank,
            embedding=embedding,
            limit=fetch,
            filters=filters,
        )
    if strategy in ("hybrid", "lexical"):
        lexical = _lexical_candidates(
            session,
            tenant_id=ctx.tenant_id,
            acl_keys=acl_keys,
            clearance_rank=clearance_rank,
            query=query,
            limit=fetch,
            filters=filters,
        )

    fused = _fuse(semantic, lexical, strategy)
    chunks = [
        RetrievedChunk(
            chunk_id=str(row["chunk_id"]),
            document_id=str(row["document_id"]),
            document_title=row["title"],
            content=row["content"],
            score=row["_score"],
            classification=str(row["classification"]),
            section_path=row.get("section_path") or "",
            page_from=row.get("page_from"),
            semantic_rank=row.get("_semantic_rank"),
            lexical_rank=row.get("_lexical_rank"),
            metadata=row.get("metadata") or {},
        )
        for row in fused[:top_k]
    ]

    before_acl, after_acl = _corpus_visibility(session, ctx.tenant_id, acl_keys, clearance_rank, filters)
    latency_ms = int((time.perf_counter() - started) * 1000)

    if record_query:
        session.execute(
            text(
                """
                INSERT INTO retrieval_queries (tenant_id, run_id, user_id, agent_key, query_text,
                                               strategy, filters, candidates_before_acl,
                                               candidates_after_acl, returned_count, latency_ms)
                VALUES (:t, :run, :u, :a, :q, :s, CAST(:f AS jsonb), :before, :after, :ret, :lat)
                """
            ),
            {
                "t": ctx.tenant_id,
                "run": ctx.run_id or None,
                "u": ctx.human.user_id if ctx.human else None,
                "a": ctx.agent.agent_id if ctx.agent else "",
                "q": query[:2000],
                "s": strategy,
                "f": __import__("json").dumps(filters, default=str),
                "before": before_acl,
                "after": after_acl,
                "ret": len(chunks),
                "lat": latency_ms,
            },
        )

    return RetrievalResult(
        query=query,
        strategy=strategy,
        chunks=chunks,
        candidates_before_acl=before_acl,
        candidates_after_acl=after_acl,
        latency_ms=latency_ms,
        clearance_ceiling=clearance,
    )


def _fuse(semantic: list[dict], lexical: list[dict], strategy: str) -> list[dict]:
    """Reciprocal rank fusion — no score normalisation needed between systems."""
    merged: dict[str, dict] = {}

    for rank, row in enumerate(semantic, start=1):
        key = str(row["chunk_id"])
        entry = merged.setdefault(key, {**row, "_score": 0.0})
        entry["_semantic_rank"] = rank
        entry["_score"] += 1.0 / (RRF_K + rank)

    for rank, row in enumerate(lexical, start=1):
        key = str(row["chunk_id"])
        entry = merged.setdefault(key, {**row, "_score": 0.0})
        entry["_lexical_rank"] = rank
        entry["_score"] += 1.0 / (RRF_K + rank)

    ordered = sorted(merged.values(), key=lambda r: -r["_score"])
    for row in ordered:
        row.setdefault("_semantic_rank", None)
        row.setdefault("_lexical_rank", None)
    return ordered


def fetch_document(
    session: Session,
    ctx: ExecutionContext,
    document_id: str,
    *,
    max_chars: int = 20000,
    agent_clearance_ceiling: str | None = None,
) -> dict[str, Any]:
    """Fetch one document, enforcing the same ACL predicate as search."""
    clearance_rank = classification_rank(effective_clearance(ctx, agent_clearance_ceiling))
    row = (
        session.execute(
            text(
                """
            SELECT d.id, d.title, d.classification, d.source_system, d.mime_type,
                   d.parse_confidence, d.unsupported_elements, d.page_count,
                   string_agg(c.content, E'\\n\\n' ORDER BY c.chunk_index) AS content
            FROM documents d
            JOIN chunks c ON c.document_id = d.id AND c.tenant_id = d.tenant_id
            WHERE d.tenant_id = :tenant AND d.id = CAST(:doc AS uuid)
              AND d.ingest_status = 'PUBLISHED' AND d.deleted_at IS NULL
              AND c.acl_principals && CAST(:acl_keys AS text[])
              AND CASE c.classification
                    WHEN 'PUBLIC' THEN 0 WHEN 'INTERNAL' THEN 1
                    WHEN 'CONFIDENTIAL' THEN 2 ELSE 3 END <= :clearance_rank
            GROUP BY d.id, d.title, d.classification, d.source_system, d.mime_type,
                     d.parse_confidence, d.unsupported_elements, d.page_count
            """
            ),
            {
                "tenant": ctx.tenant_id,
                "doc": document_id,
                "acl_keys": principal_acl_keys(ctx),
                "clearance_rank": clearance_rank,
            },
        )
        .mappings()
        .first()
    )

    if row is None:
        # Deliberately indistinguishable from "does not exist": a caller must
        # not be able to probe for the existence of documents they cannot read.
        raise AuthorizationError("document not found or not accessible", details={"document_id": document_id})

    content = row["content"] or ""
    truncated = len(content) > max_chars
    return {
        "document_id": str(row["id"]),
        "title": row["title"],
        "classification": str(row["classification"]),
        "source_system": row["source_system"],
        "mime_type": row["mime_type"],
        "page_count": row["page_count"],
        "parse_confidence": float(row["parse_confidence"]) if row["parse_confidence"] else None,
        "unsupported_elements": list(row["unsupported_elements"] or []),
        "content": content[:max_chars],
        "truncated": truncated,
    }


def verify_citations(
    claims: list[dict[str, Any]], sources: list[dict[str, Any]], *, min_overlap: float = 0.6
) -> dict[str, Any]:
    """Check that each claim's cited source actually contains supporting text.

    Support is measured as content-word overlap between the claim and the cited
    source. It catches the common failure — a citation attached to a claim the
    source does not make — without pretending to be entailment checking.
    """
    import re

    def words(value: str) -> set[str]:
        stop = {
            "the",
            "a",
            "an",
            "and",
            "or",
            "of",
            "in",
            "on",
            "to",
            "for",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "with",
            "that",
            "this",
            "at",
            "by",
            "from",
            "it",
        }
        return {w for w in re.findall(r"[a-z0-9']+", value.lower()) if w not in stop and len(w) > 2}

    index = {
        str(s.get("id", s.get("chunk_id", ""))): str(s.get("text", s.get("content", ""))) for s in sources
    }
    verified: list[dict] = []
    unverified: list[dict] = []

    for claim in claims:
        statement = str(claim.get("statement", claim.get("text", "")))
        cited = [str(c) for c in claim.get("citations", [])]
        claim_words = words(statement)
        if not claim_words:
            unverified.append({**claim, "reason": "claim contains no content words"})
            continue
        if not cited:
            unverified.append({**claim, "reason": "claim carries no citation"})
            continue

        best = 0.0
        best_source = ""
        for citation in cited:
            source_text = index.get(citation)
            if source_text is None:
                continue
            overlap = len(claim_words & words(source_text)) / len(claim_words)
            if overlap > best:
                best, best_source = overlap, citation

        if best >= min_overlap:
            verified.append({**claim, "supported_by": best_source, "overlap": round(best, 3)})
        else:
            unverified.append(
                {
                    **claim,
                    "reason": (
                        "cited source not found"
                        if not best_source
                        else f"insufficient support (overlap {best:.2f} < {min_overlap})"
                    ),
                    "overlap": round(best, 3),
                }
            )

    total = len(claims)
    return {
        "verified": verified,
        "unverified": unverified,
        "coverage": round(len(verified) / total, 3) if total else 0.0,
        "min_overlap": min_overlap,
    }
