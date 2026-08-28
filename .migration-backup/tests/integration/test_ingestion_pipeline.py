"""Governed ingestion: every gate must be able to stop a document."""

from __future__ import annotations

import pytest
from agentic_os.core.context import ExecutionContext, HumanIdentity
from agentic_os.knowledge import ingestion, pii
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


@pytest.fixture()
def ictx(db: Session, tenant_id: str, organization_id: str) -> ExecutionContext:
    user = db.execute(
        text("SELECT id FROM users WHERE tenant_id = :t AND email = 'admin@rta.example'"),
        {"t": tenant_id},
    ).one()
    return ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(
            user_id=str(user.id),
            email="admin@rta.example",
            roles=frozenset({"platform_admin"}),
            permissions=frozenset({"*"}),
            clearance="RESTRICTED",
        ),
    )


def _ingest(db, ctx, body: bytes, name: str, mime: str = "text/markdown", **kwargs):
    return ingestion.ingest(db, ctx, data=body, filename=name, mime_type=mime, title=name, **kwargs)


# ------------------------------------------------------------------- happy path
def test_clean_document_is_published_with_chunks_and_embeddings(db: Session, ictx) -> None:
    body = (
        "# Point Machine Maintenance\n\n"
        "Point machine PM-14 requires lubrication every 60 days. The actuator stroke "
        "must be measured at each service and recorded against asset AST-6003.\n\n"
        "## Failure history\n\n"
        "Two actuator faults were recorded in the last twelve months, both traced to "
        "contamination of the lubricant."
    )
    result = _ingest(db, ictx, body.encode(), "point-machine-probe.md")
    assert result.published, result.rejection_reason
    assert result.chunk_count >= 1

    stages = {s.stage for s in result.stages}
    for required in (
        "quarantine",
        "malware_scan",
        "validation",
        "parse",
        "pii_dlp",
        "acl_inheritance",
        "index",
        "quality_gate",
    ):
        assert required in stages, f"stage '{required}' did not run"

    embeddings = db.execute(
        text(
            "SELECT count(*) FROM embeddings e JOIN chunks c ON c.id = e.chunk_id "
            "WHERE c.document_id = CAST(:d AS uuid)"
        ),
        {"d": result.document_id},
    ).scalar_one()
    assert embeddings == result.chunk_count, "every chunk must be embedded"
    db.rollback()


def test_identical_content_is_deduplicated(db: Session, ictx) -> None:
    body = b"# Dedup probe\n\nThe same content ingested twice must not create two documents."
    first = _ingest(db, ictx, body, "dedup-a.md")
    second = _ingest(db, ictx, body, "dedup-b.md")
    assert second.document_id == first.document_id
    assert any(s.stage == "deduplication" for s in second.stages)
    db.rollback()


# ------------------------------------------------------------------- gates
def test_executable_content_is_rejected_at_the_scan_gate(db: Session, ictx) -> None:
    result = _ingest(db, ictx, b"MZ\x90\x00executable payload", "payload.md")
    assert not result.published
    assert result.status == "REJECTED"
    stage = next(s for s in result.stages if s.stage == "malware_scan")
    assert stage.passed is False
    assert "executable" in str(stage.detail["findings"]).lower()
    db.rollback()


def test_scan_result_is_recorded_on_the_document(db: Session, ictx) -> None:
    result = _ingest(db, ictx, b"\x7fELFbinary", "elf.md")
    status = db.execute(
        text("SELECT malware_scan_status FROM documents WHERE id = CAST(:d AS uuid)"),
        {"d": result.document_id},
    ).scalar_one()
    assert status == "INFECTED"
    db.rollback()


def test_path_traversal_in_the_filename_is_rejected(db: Session, ictx) -> None:
    result = _ingest(db, ictx, b"# harmless\n\nbody text here for the parser", "../../etc/passwd")
    assert not result.published
    assert any(s.stage == "validation" and not s.passed for s in result.stages)
    db.rollback()


def test_unparseable_type_is_declared_not_silently_empty(db: Session, ictx) -> None:
    result = _ingest(db, ictx, b"\x89PNG\r\n\x1a\n", "scan.png", mime="image/png")
    assert not result.published
    stage = next(s for s in result.stages if s.stage == "parse")
    assert "OCR" in stage.detail["error"]
    db.rollback()


def test_xml_with_a_doctype_is_rejected(db: Session, ictx) -> None:
    payload = b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>'
    result = _ingest(db, ictx, payload, "entity.xml", mime="application/xml")
    assert not result.published
    db.rollback()


# --------------------------------------------------------------------- privacy
def test_pii_raises_document_classification(db: Session, ictx) -> None:
    body = (
        "# Contact register\n\n"
        "Primary contact: contact@rta.example, telephone +971 4 284 4444.\n"
        "National identifier: 784-1985-1234567-1.\n"
        "Payment card on file: 4111 1111 1111 1111."
    )
    result = _ingest(db, ictx, body.encode(), "contacts-probe.md", declared_classification="INTERNAL")
    assert result.published, result.rejection_reason
    assert result.classification == "RESTRICTED", (
        "restricted identifiers must raise the record's classification"
    )
    assert "PAYMENT_CARD" in result.pii_labels
    assert "EMIRATES_ID" in result.pii_labels

    inventory = db.execute(
        text("SELECT count(*) FROM pii_inventory WHERE tenant_id = :t AND resource_id = :r"),
        {"t": ictx.tenant_id, "r": result.document_id},
    ).scalar_one()
    assert inventory >= 3, "each finding must be inventoried"
    db.rollback()


@pytest.mark.unit
def test_redaction_leaves_no_residual_fragments() -> None:
    body = (
        "Contact person@example.test or +971 4 284 4444. Emirates ID 784-1985-1234567-1. "
        "Card 4111 1111 1111 1111. Key sk-abcdefghij0123456789. Host 10.0.4.22."
    )
    redacted = pii.redact(body)
    for fragment in ("person@example.test", "971 4 284", "784-1985", "4111", "sk-abcdefghij", "10.0.4.22"):
        assert fragment not in redacted, f"'{fragment}' survived redaction"


@pytest.mark.unit
def test_ordinary_operational_text_is_not_mislabelled_as_pii() -> None:
    clean = (
        "Work order WO-4471 closed on 2026-03-12 after 14.5 hours, cost 8200 AED, "
        "availability 97.4 percent for asset AST-4012."
    )
    assert pii.classify(clean)["labels"] == [], "false PII labels would wrongly restrict access"


@pytest.mark.unit
def test_pii_coverage_gaps_are_declared() -> None:
    report = pii.classify("some text")
    assert "PERSON_NAME" in report["unsupported_types"]
    assert report["detectors_run"], "the report must name which detectors ran"


# ------------------------------------------------------------------------ ACL
def test_acl_is_inherited_by_every_chunk(db: Session, ictx) -> None:
    result = _ingest(
        db,
        ictx,
        b"# ACL probe\n\nThis document is restricted to a single group for testing purposes. "
        b"It exists so that the access control inheritance path has a real document to "
        b"assert against, and carries enough text to clear the quality gate.",
        "acl-probe.md",
        acl=[{"principal_type": "GROUP", "principal_id": "systems-section"}],
    )
    assert result.published
    principals = (
        db.execute(
            text("SELECT DISTINCT acl_principals FROM chunks WHERE document_id = CAST(:d AS uuid)"),
            {"d": result.document_id},
        )
        .scalars()
        .all()
    )
    assert principals, "chunks must carry inherited ACL principals"
    for entry in principals:
        assert "GROUP:systems-section" in entry
    db.rollback()


def test_rejected_documents_are_never_retrievable(db: Session, ictx) -> None:
    from agentic_os.knowledge import retrieval

    result = _ingest(db, ictx, b"MZ\x90rejected binary content", "rejected.md")
    assert not result.published
    found = retrieval.search(db, ictx, "rejected binary content", top_k=20)
    assert result.document_id not in {c.document_id for c in found.chunks}
    db.rollback()
