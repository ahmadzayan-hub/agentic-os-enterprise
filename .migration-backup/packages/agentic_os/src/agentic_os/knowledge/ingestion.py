"""Governed ingestion pipeline.

    Upload -> Quarantine -> Malware scan -> Validation -> Parse -> PII/DLP ->
    Classification -> ACL inheritance -> Chunk -> Embed -> Index ->
    Graph extraction -> Quality gate -> Publish

Every stage records its outcome on the document. A document only reaches
``PUBLISHED`` after passing every gate; anything that fails stops at the stage
that failed, with the reason recorded, and is never retrievable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.assurance.audit import AuditEntry, AuditLedger
from agentic_os.core.context import ExecutionContext
from agentic_os.core.crypto import sha256_hex
from agentic_os.core.errors import ValidationError
from agentic_os.core.ids import utcnow
from agentic_os.knowledge import pii
from agentic_os.knowledge.chunking import chunk_text
from agentic_os.knowledge.embeddings import get_embedder
from agentic_os.knowledge.parsers import parse

MAX_DOCUMENT_BYTES = 64 * 1024 * 1024

#: Byte signatures of executable formats that must never be parsed as
#: documents. This is a structural check, not an antivirus engine.
_DANGEROUS_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"MZ", "DOS/Windows executable"),
    (b"\x7fELF", "ELF executable"),
    (b"\xca\xfe\xba\xbe", "Java class or Mach-O fat binary"),
    (b"#!", "script with a shebang"),
    (b"\xd0\xcf\x11\xe0", "OLE compound file (legacy Office with macro risk)"),
)

_MACRO_MARKERS = (b"vbaProject.bin", b"macroEnabled")


@dataclass(slots=True)
class StageResult:
    stage: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IngestionResult:
    document_id: str
    status: str
    stages: list[StageResult] = field(default_factory=list)
    chunk_count: int = 0
    classification: str = "INTERNAL"
    parse_confidence: float = 0.0
    unsupported_elements: list[str] = field(default_factory=list)
    pii_labels: list[str] = field(default_factory=list)
    rejection_reason: str = ""

    @property
    def published(self) -> bool:
        return self.status == "PUBLISHED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "status": self.status,
            "published": self.published,
            "chunk_count": self.chunk_count,
            "classification": self.classification,
            "parse_confidence": self.parse_confidence,
            "unsupported_elements": self.unsupported_elements,
            "pii_labels": self.pii_labels,
            "rejection_reason": self.rejection_reason,
            "stages": [{"stage": s.stage, "passed": s.passed, **s.detail} for s in self.stages],
        }


def scan_for_malware(data: bytes, mime_type: str) -> StageResult:
    """Structural safety scan.

    Deliberately *not* described as antivirus. It rejects executable content
    and macro-enabled Office files, which covers the delivery vectors that
    matter for a document ingestion path, and records that a signature-based
    scanner is not wired so the gap is visible in the evidence record.
    """
    findings: list[str] = []
    header = data[:8]
    for signature, description in _DANGEROUS_SIGNATURES:
        if header.startswith(signature):
            findings.append(f"executable content detected: {description}")

    if mime_type.startswith("application/vnd.openxmlformats"):
        for marker in _MACRO_MARKERS:
            if marker in data[: 4 * 1024 * 1024]:
                findings.append("macro-enabled Office document")
                break

    return StageResult(
        stage="malware_scan",
        passed=not findings,
        detail={
            "findings": findings,
            "scanner": "structural-signature-v1",
            "external_scanner_configured": False,
            "note": (
                "structural checks only; connect an ICAP or ClamAV scanner for signature-based detection"
            ),
        },
    )


def validate_upload(data: bytes, filename: str, mime_type: str) -> StageResult:
    problems: list[str] = []
    if not data:
        problems.append("file is empty")
    if len(data) > MAX_DOCUMENT_BYTES:
        problems.append(f"file exceeds {MAX_DOCUMENT_BYTES} bytes")
    if not filename or filename in (".", ".."):
        problems.append("filename is missing or invalid")
    if re.search(r"[\x00-\x1f]|\.\./|^/", filename or ""):
        problems.append("filename contains path traversal or control characters")
    if not mime_type:
        problems.append("mime type is required")
    return StageResult(
        stage="validation",
        passed=not problems,
        detail={"problems": problems, "byte_size": len(data), "mime_type": mime_type},
    )


def _acl_principals(acl_entries: list[dict[str, str]]) -> list[str]:
    return sorted({f"{e['principal_type']}:{e['principal_id']}" for e in acl_entries})


def ingest(
    session: Session,
    ctx: ExecutionContext,
    *,
    data: bytes,
    filename: str,
    mime_type: str,
    title: str = "",
    source_system: str = "upload",
    declared_classification: str = "INTERNAL",
    owner_team: str = "",
    acl: list[dict[str, str]] | None = None,
    language: str = "en",
    retention_days: int | None = None,
    extract_graph: bool = True,
) -> IngestionResult:
    """Run a document through the full governed pipeline."""
    stages: list[StageResult] = []
    content_hash = sha256_hex(data)
    title = title or filename

    acl = list(acl or [])
    if ctx.human is not None and not any(
        e["principal_type"] == "USER" and e["principal_id"] == ctx.human.user_id for e in acl
    ):
        acl.append({"principal_type": "USER", "principal_id": ctx.human.user_id, "permission": "OWNER"})
    if not acl:
        raise ValidationError("a document must have at least one access control entry")

    existing = (
        session.execute(
            text(
                "SELECT id, ingest_status FROM documents "
                "WHERE tenant_id = :t AND content_hash = :h AND deleted_at IS NULL"
            ),
            {"t": ctx.tenant_id, "h": content_hash},
        )
        .mappings()
        .first()
    )
    if existing is not None:
        return IngestionResult(
            document_id=str(existing["id"]),
            status=str(existing["ingest_status"]),
            stages=[StageResult("deduplication", True, {"reason": "identical content already ingested"})],
        )

    # -- stage 1: quarantine ------------------------------------------------
    row = session.execute(
        text(
            """
            INSERT INTO documents (tenant_id, organization_id, title, source_system, mime_type,
                                   byte_size, content_hash, classification, owner_user_id,
                                   owner_team, language, ingest_status, retention_until)
            VALUES (:t, :o, :title, :src, :mime, :size, :hash,
                    CAST(:cls AS data_classification), :owner, :team, :lang,
                    'QUARANTINED', :retention)
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id,
            "o": ctx.organization_id,
            "title": title[:500],
            "src": source_system,
            "mime": mime_type,
            "size": len(data),
            "hash": content_hash,
            "cls": declared_classification,
            "owner": ctx.human.user_id if ctx.human else None,
            "team": owner_team,
            "lang": language,
            "retention": utcnow() + timedelta(days=retention_days) if retention_days else None,
        },
    ).one()
    document_id = str(row.id)
    stages.append(StageResult("quarantine", True, {"content_hash": content_hash[:16]}))

    def fail(stage: StageResult, status: str = "REJECTED") -> IngestionResult:
        stages.append(stage)
        reason = json.dumps(stage.detail, default=str)[:2000]
        session.execute(
            text(
                "UPDATE documents SET ingest_status = :s, rejection_reason = :r, "
                "ingest_stage_detail = CAST(:d AS jsonb), updated_at = now() WHERE id = :i"
            ),
            {
                "s": status,
                "r": reason,
                "d": json.dumps(
                    [{"stage": x.stage, "passed": x.passed, **x.detail} for x in stages],
                    default=str,
                ),
                "i": document_id,
            },
        )
        AuditLedger(session).append(
            ctx,
            AuditEntry(
                category="DATA_ACCESS",
                action="document.ingest_rejected",
                outcome="DENIED",
                resource_type="document",
                resource_id=document_id,
                payload={"stage": stage.stage, "detail": stage.detail},
            ),
        )
        return IngestionResult(document_id=document_id, status=status, stages=stages, rejection_reason=reason)

    # -- stage 2: malware scan ---------------------------------------------
    session.execute(text("UPDATE documents SET ingest_status = 'SCANNING' WHERE id = :i"), {"i": document_id})
    scan = scan_for_malware(data, mime_type)
    session.execute(
        text("UPDATE documents SET malware_scan_status = :s WHERE id = :i"),
        {"s": "CLEAN" if scan.passed else "INFECTED", "i": document_id},
    )
    if not scan.passed:
        return fail(scan)
    stages.append(scan)

    # -- stage 3: validation ------------------------------------------------
    validation = validate_upload(data, filename, mime_type)
    if not validation.passed:
        return fail(validation)
    stages.append(validation)

    # -- stage 4: parse -----------------------------------------------------
    session.execute(text("UPDATE documents SET ingest_status = 'PARSING' WHERE id = :i"), {"i": document_id})
    try:
        parsed = parse(data, mime_type)
    except Exception as exc:
        return fail(StageResult("parse", False, {"error": str(exc)}))
    if not parsed.text.strip():
        return fail(
            StageResult(
                "parse",
                False,
                {
                    "error": "no extractable text",
                    "unsupported_elements": parsed.unsupported_elements,
                },
            )
        )
    stages.append(StageResult("parse", True, parsed.to_dict()))

    # -- stage 5: PII detection and DLP classification ----------------------
    session.execute(
        text("UPDATE documents SET ingest_status = 'ENRICHING' WHERE id = :i"), {"i": document_id}
    )
    dlp = pii.classify(parsed.text, declared_classification=declared_classification)
    classification = dlp["classification"]
    stages.append(
        StageResult(
            "pii_dlp",
            True,
            {
                "classification": classification,
                "raised": dlp["raised"],
                "labels": dlp["labels"],
                "finding_count": dlp["finding_count"],
                "unsupported_types": dlp["unsupported_types"],
            },
        )
    )
    for finding in dlp["findings"][:200]:
        session.execute(
            text(
                """
                INSERT INTO pii_inventory (tenant_id, resource_type, resource_id, pii_type,
                                           detector, confidence)
                VALUES (:t, 'document', :r, :type, :det, :conf)
                """
            ),
            {
                "t": ctx.tenant_id,
                "r": document_id,
                "type": finding["pii_type"],
                "det": finding["detector"],
                "conf": finding["confidence"],
            },
        )

    # -- stage 6: ACL inheritance ------------------------------------------
    for entry in acl:
        session.execute(
            text(
                """
                INSERT INTO document_acl (tenant_id, document_id, principal_type,
                                          principal_id, permission)
                VALUES (:t, :d, :pt, :pi, :perm)
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "t": ctx.tenant_id,
                "d": document_id,
                "pt": entry["principal_type"],
                "pi": entry["principal_id"],
                "perm": entry.get("permission", "READ"),
            },
        )
    acl_principals = _acl_principals(acl)
    stages.append(StageResult("acl_inheritance", True, {"principals": acl_principals}))

    # -- stage 7: chunk, embed, index --------------------------------------
    session.execute(text("UPDATE documents SET ingest_status = 'INDEXING' WHERE id = :i"), {"i": document_id})
    chunks = chunk_text(parsed.text)
    if not chunks:
        return fail(StageResult("chunking", False, {"error": "document produced no chunks"}))

    embedder = get_embedder()
    vectors = embedder.embed([c.content for c in chunks])
    for chunk, vector in zip(chunks, vectors, strict=True):
        chunk_row = session.execute(
            text(
                """
                INSERT INTO chunks (tenant_id, document_id, document_version, chunk_index,
                                    content, content_hash, token_count, section_path,
                                    classification, metadata, acl_principals)
                VALUES (:t, :d, 1, :idx, :content, :hash, :tokens, :section,
                        CAST(:cls AS data_classification), CAST(:meta AS jsonb), :acl)
                RETURNING id
                """
            ),
            {
                "t": ctx.tenant_id,
                "d": document_id,
                "idx": chunk.index,
                "content": chunk.content,
                "hash": sha256_hex(chunk.content),
                "tokens": chunk.token_count,
                "section": chunk.section_path,
                "cls": classification,
                "meta": json.dumps(
                    {
                        "char_start": chunk.char_start,
                        "char_end": chunk.char_end,
                        "source_file": filename,
                    }
                ),
                "acl": acl_principals,
            },
        ).one()
        session.execute(
            text(
                """
                INSERT INTO embeddings (tenant_id, chunk_id, model_key, dimensions, embedding)
                VALUES (:t, :c, :m, :dim, CAST(:vec AS vector))
                """
            ),
            {
                "t": ctx.tenant_id,
                "c": chunk_row.id,
                "m": embedder.name,
                "dim": embedder.dimensions,
                "vec": str(vector),
            },
        )
    stages.append(
        StageResult(
            "index",
            True,
            {
                "chunks": len(chunks),
                "embedding_model": embedder.name,
                "dimensions": embedder.dimensions,
            },
        )
    )

    # -- stage 8: graph extraction -----------------------------------------
    if extract_graph:
        from agentic_os.knowledge.graph import extract_from_document

        nodes, edges = extract_from_document(
            session, ctx, document_id=document_id, title=title, content=parsed.text
        )
        stages.append(StageResult("graph_extraction", True, {"nodes": nodes, "edges": edges}))

    # -- stage 9: quality gate ---------------------------------------------
    quality_problems: list[str] = []
    if parsed.confidence < 0.5:
        quality_problems.append(f"parse confidence {parsed.confidence} below 0.5")
    if len(chunks) == 1 and chunks[0].token_count < 20:
        quality_problems.append("document contains too little text to be useful")
    if quality_problems:
        return fail(StageResult("quality_gate", False, {"problems": quality_problems}))
    stages.append(StageResult("quality_gate", True, {"parse_confidence": parsed.confidence}))

    # -- stage 10: publish --------------------------------------------------
    session.execute(
        text(
            """
            UPDATE documents
               SET ingest_status = 'PUBLISHED',
                   classification = CAST(:cls AS data_classification),
                   dlp_labels = :labels,
                   pii_findings = CAST(:pii AS jsonb),
                   parse_confidence = :conf,
                   unsupported_elements = :unsupported,
                   page_count = :pages,
                   ingest_stage_detail = CAST(:stages AS jsonb),
                   updated_at = now()
             WHERE id = :i
            """
        ),
        {
            "cls": classification,
            "labels": dlp["labels"],
            "pii": json.dumps(
                [{"pii_type": f["pii_type"], "confidence": f["confidence"]} for f in dlp["findings"]]
            ),
            "conf": parsed.confidence,
            "unsupported": parsed.unsupported_elements,
            "pages": parsed.page_count,
            "stages": json.dumps(
                [{"stage": s.stage, "passed": s.passed, **s.detail} for s in stages], default=str
            ),
            "i": document_id,
        },
    )
    session.execute(
        text(
            """
            INSERT INTO document_versions (tenant_id, document_id, version, content_hash,
                                           byte_size, created_by)
            VALUES (:t, :d, 1, :h, :size, :by)
            ON CONFLICT (document_id, version) DO NOTHING
            """
        ),
        {
            "t": ctx.tenant_id,
            "d": document_id,
            "h": content_hash,
            "size": len(data),
            "by": ctx.human.user_id if ctx.human else None,
        },
    )

    AuditLedger(session).append(
        ctx,
        AuditEntry(
            category="DATA_ACCESS",
            action="document.published",
            resource_type="document",
            resource_id=document_id,
            classification=classification,
            payload={
                "title": title,
                "chunks": len(chunks),
                "classification": classification,
                "pii_labels": dlp["labels"],
                "acl_principals": acl_principals,
            },
        ),
    )

    return IngestionResult(
        document_id=document_id,
        status="PUBLISHED",
        stages=stages,
        chunk_count=len(chunks),
        classification=classification,
        parse_confidence=parsed.confidence,
        unsupported_elements=parsed.unsupported_elements,
        pii_labels=dlp["labels"],
    )
