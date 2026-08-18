"""Knowledge surfaces: search, documents, datasets and the G-Brain graph."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text

from agentic_os.api.deps import CtxDep, DbDep, require_permission
from agentic_os.api.serialization import jsonify, row as json_row, rows as json_rows
from agentic_os.core.errors import AgenticError
from agentic_os.knowledge import graph, ingestion, retrieval

router = APIRouter(tags=["knowledge"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    top_k: int = Field(default=8, ge=1, le=50)
    strategy: str = Field(default="hybrid", pattern="^(hybrid|semantic|lexical)$")


@router.post(
    "/knowledge/search",
    dependencies=[Depends(require_permission("knowledge:read", resource_type="document"))],
)
def search(payload: SearchRequest, ctx: CtxDep, db: DbDep) -> dict:
    try:
        result = retrieval.search(
            db, ctx, payload.query, top_k=payload.top_k, strategy=payload.strategy
        )
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    return jsonify(result.to_dict())


@router.get(
    "/documents",
    dependencies=[Depends(require_permission("knowledge:read", resource_type="document"))],
)
def list_documents(ctx: CtxDep, db: DbDep, limit: Annotated[int, Query(le=200)] = 100) -> dict:
    """Documents the caller may see, with their ingestion outcome."""
    rows = db.execute(
        text(
            """
            SELECT DISTINCT d.id, d.title, d.source_system, d.mime_type, d.byte_size,
                   d.classification, d.owner_team, d.ingest_status, d.malware_scan_status,
                   d.dlp_labels, d.parse_confidence, d.unsupported_elements, d.page_count,
                   d.rejection_reason, d.created_at,
                   (SELECT count(*) FROM chunks c WHERE c.document_id = d.id) AS chunk_count
            FROM documents d
            JOIN chunks ch ON ch.document_id = d.id AND ch.tenant_id = d.tenant_id
            WHERE d.tenant_id = :t AND d.deleted_at IS NULL
              AND ch.acl_principals && CAST(:acl AS text[])
              AND CASE ch.classification
                    WHEN 'PUBLIC' THEN 0 WHEN 'INTERNAL' THEN 1
                    WHEN 'CONFIDENTIAL' THEN 2 ELSE 3 END <= :rank
            ORDER BY d.created_at DESC
            LIMIT :l
            """
        ),
        {
            "t": ctx.tenant_id,
            "acl": retrieval.principal_acl_keys(ctx),
            "rank": __import__(
                "agentic_os.core.context", fromlist=["classification_rank"]
            ).classification_rank(ctx.human.clearance if ctx.human else "PUBLIC"),
            "l": limit,
        },
    ).mappings()
    return {"documents": json_rows(rows)}


@router.get(
    "/documents/{document_id}",
    dependencies=[Depends(require_permission("knowledge:read", resource_type="document"))],
)
def get_document(document_id: str, ctx: CtxDep, db: DbDep) -> dict:
    try:
        return jsonify(retrieval.fetch_document(db, ctx, document_id))
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc


@router.post(
    "/documents",
    dependencies=[Depends(require_permission("knowledge:write", resource_type="document"))],
)
async def upload_document(
    ctx: CtxDep,
    db: DbDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()] = "",
    classification: Annotated[str, Form()] = "INTERNAL",
    owner_team: Annotated[str, Form()] = "",
) -> dict:
    """Upload a document through the full governed ingestion pipeline."""
    from agentic_os.core.config import get_settings

    data = await file.read()
    if len(data) > get_settings().max_upload_bytes:
        raise HTTPException(status_code=413, detail={"error": "VALIDATION", "message": "too large"})
    try:
        result = ingestion.ingest(
            db,
            ctx,
            data=data,
            filename=file.filename or "upload",
            mime_type=file.content_type or "application/octet-stream",
            title=title or file.filename or "upload",
            declared_classification=classification,
            owner_team=owner_team,
        )
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
    return result.to_dict()


@router.get(
    "/datasets", dependencies=[Depends(require_permission("knowledge:read", resource_type="dataset"))]
)
def list_datasets(ctx: CtxDep, db: DbDep) -> dict:
    rows = db.execute(
        text(
            "SELECT dataset_key, name, description, source_system, owner_team, classification, "
            "schema_fields, primary_key_field, row_count, quality_score, quality_detail, "
            "freshness_at, status FROM datasets WHERE tenant_id = :t ORDER BY dataset_key"
        ),
        {"t": ctx.tenant_id},
    ).mappings()
    return {"datasets": json_rows(rows)}


@router.get("/graph", dependencies=[Depends(require_permission("graph:read", resource_type="graph"))])
def query_graph(
    ctx: CtxDep,
    db: DbDep,
    node_key: str = "",
    node_type: str = "",
    depth: Annotated[int, Query(ge=1, le=4)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    try:
        return jsonify(
            graph.query(
                db, ctx, node_key=node_key, node_type=node_type, depth=depth, limit=limit
            )
        )
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc


@router.get(
    "/graph/impact",
    dependencies=[Depends(require_permission("graph:read", resource_type="graph"))],
)
def impact(
    ctx: CtxDep,
    db: DbDep,
    node_key: str,
    depth: Annotated[int, Query(ge=1, le=5)] = 3,
    direction: str = "downstream",
) -> dict:
    try:
        return jsonify(
            graph.impact_analysis(db, ctx, node_key=node_key, depth=depth, direction=direction)
        )
    except AgenticError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc
