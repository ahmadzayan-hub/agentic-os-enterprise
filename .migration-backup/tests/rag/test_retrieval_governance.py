"""Retrieval quality and, above all, retrieval leakage.

The tests that matter here are the negative ones: a principal must not be able
to retrieve, rank, count or infer the existence of content they cannot read.
"""

from __future__ import annotations

import pytest
from agentic_os.core.context import ExecutionContext, HumanIdentity
from agentic_os.core.errors import AuthorizationError
from agentic_os.knowledge import retrieval
from agentic_os.knowledge.embeddings import DeterministicHashEmbedder, cosine
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, pytest.mark.rag, requires_db]

RESTRICTED_TITLE = "Workforce Case Note (RESTRICTED)"
RESTRICTED_QUERY = "grievance escalation shift allocation employment matter"


def make_ctx(
    tenant_id: str, organization_id: str, *, clearance: str, roles: set[str], user_id: str
) -> ExecutionContext:
    return ExecutionContext(
        tenant_id=tenant_id,
        organization_id=organization_id,
        human=HumanIdentity(
            user_id=user_id,
            email="probe@example.test",
            roles=frozenset(roles),
            permissions=frozenset({"knowledge:read"}),
            clearance=clearance,  # type: ignore[arg-type]
        ),
    )


@pytest.fixture()
def user_id(db: Session, tenant_id: str) -> str:
    row = db.execute(
        text("SELECT id FROM users WHERE tenant_id = :t AND email = 'analyst@rta.example'"),
        {"t": tenant_id},
    ).one()
    return str(row.id)


# ------------------------------------------------------------------- retrieval
def test_hybrid_retrieval_finds_the_relevant_document(
    db: Session, tenant_id: str, organization_id: str, user_id: str
) -> None:
    ctx = make_ctx(tenant_id, organization_id, clearance="INTERNAL", roles={"operator"}, user_id=user_id)
    result = retrieval.search(db, ctx, "escalator step chain failure mode", top_k=5)
    assert result.chunks, "expected results from the seeded corpus"
    assert any("Escalator" in c.document_title for c in result.chunks)
    assert result.chunks[0].score > 0


def test_semantic_and_lexical_strategies_both_work(
    db: Session, tenant_id: str, organization_id: str, user_id: str
) -> None:
    ctx = make_ctx(tenant_id, organization_id, clearance="INTERNAL", roles={"operator"}, user_id=user_id)
    for strategy in ("semantic", "lexical", "hybrid"):
        result = retrieval.search(db, ctx, "brake pad wear", strategy=strategy, top_k=5)
        assert result.chunks, f"{strategy} returned nothing"
        assert result.strategy == strategy


def test_results_carry_resolvable_citations(
    db: Session, tenant_id: str, organization_id: str, user_id: str
) -> None:
    ctx = make_ctx(tenant_id, organization_id, clearance="INTERNAL", roles={"operator"}, user_id=user_id)
    result = retrieval.search(db, ctx, "rolling stock availability target", top_k=3)
    for chunk in result.chunks:
        citation = chunk.citation()
        assert citation["chunk_id"] and citation["document_id"] and citation["title"]
        exists = db.execute(
            text("SELECT 1 FROM chunks WHERE id = CAST(:c AS uuid)"), {"c": citation["chunk_id"]}
        ).first()
        assert exists is not None, "a citation must resolve to a real chunk"


# --------------------------------------------------------------------- leakage
@pytest.mark.parametrize(
    ("clearance", "roles"),
    [("PUBLIC", {"operator"}), ("INTERNAL", {"operator"}), ("CONFIDENTIAL", {"operator"})],
)
def test_restricted_content_never_leaks_below_clearance(
    db: Session, tenant_id: str, organization_id: str, user_id: str, clearance: str, roles: set
) -> None:
    ctx = make_ctx(tenant_id, organization_id, clearance=clearance, roles=roles, user_id=user_id)
    result = retrieval.search(db, ctx, RESTRICTED_QUERY, top_k=20)
    titles = {c.document_title for c in result.chunks}
    assert RESTRICTED_TITLE not in titles
    assert not any("grievance" in c.content.lower() for c in result.chunks)


def test_restricted_content_is_visible_at_the_right_clearance(
    db: Session, tenant_id: str, organization_id: str, user_id: str
) -> None:
    """The negative tests are only meaningful if the positive case works."""
    ctx = make_ctx(tenant_id, organization_id, clearance="RESTRICTED", roles={"executive"}, user_id=user_id)
    result = retrieval.search(db, ctx, RESTRICTED_QUERY, top_k=20)
    assert RESTRICTED_TITLE in {c.document_title for c in result.chunks}


def test_acl_filters_by_role_not_only_clearance(
    db: Session, tenant_id: str, organization_id: str, user_id: str
) -> None:
    """A RESTRICTED clearance without the granted role still sees nothing."""
    ctx = make_ctx(tenant_id, organization_id, clearance="RESTRICTED", roles={"builder"}, user_id=user_id)
    result = retrieval.search(db, ctx, RESTRICTED_QUERY, top_k=20)
    assert RESTRICTED_TITLE not in {c.document_title for c in result.chunks}


def test_fetch_document_denies_unauthorised_and_hides_existence(
    db: Session, tenant_id: str, organization_id: str, user_id: str
) -> None:
    restricted_id = db.execute(
        text("SELECT id FROM documents WHERE tenant_id = :t AND title = :ti"),
        {"t": tenant_id, "ti": RESTRICTED_TITLE},
    ).scalar_one()

    ctx = make_ctx(tenant_id, organization_id, clearance="INTERNAL", roles={"operator"}, user_id=user_id)
    with pytest.raises(AuthorizationError) as denied:
        retrieval.fetch_document(db, ctx, str(restricted_id))

    with pytest.raises(AuthorizationError) as missing:
        retrieval.fetch_document(db, ctx, "00000000-0000-0000-0000-000000000000")

    # The two messages must be indistinguishable, otherwise the error itself
    # discloses that the restricted document exists.
    assert denied.value.message == missing.value.message


def test_cross_tenant_retrieval_returns_nothing(
    db_other: Session, other_tenant_id: str, organization_id: str
) -> None:
    other_user = db_other.execute(
        text("SELECT id FROM users WHERE tenant_id = :t LIMIT 1"), {"t": other_tenant_id}
    ).scalar_one()
    ctx = make_ctx(
        other_tenant_id,
        organization_id,
        clearance="RESTRICTED",
        roles={"platform_admin"},
        user_id=str(other_user),
    )
    result = retrieval.search(db_other, ctx, "escalator step chain failure", top_k=20)
    assert result.chunks == []
    assert result.candidates_before_acl == 0, "the other tenant's corpus must be invisible"


def test_unauthenticated_retrieval_is_refused(db: Session, tenant_id: str) -> None:
    ctx = ExecutionContext(tenant_id=tenant_id, organization_id="org")
    with pytest.raises(AuthorizationError):
        retrieval.search(db, ctx, "anything at all")


def test_agent_ceiling_further_restricts_a_cleared_human(
    db: Session, tenant_id: str, organization_id: str, user_id: str
) -> None:
    """An agent can never widen what the human could see, only narrow it."""
    ctx = make_ctx(tenant_id, organization_id, clearance="RESTRICTED", roles={"executive"}, user_id=user_id)
    unrestricted = retrieval.search(db, ctx, RESTRICTED_QUERY, top_k=20)
    assert RESTRICTED_TITLE in {c.document_title for c in unrestricted.chunks}

    via_agent = retrieval.search(db, ctx, RESTRICTED_QUERY, top_k=20, agent_clearance_ceiling="INTERNAL")
    assert RESTRICTED_TITLE not in {c.document_title for c in via_agent.chunks}
    assert via_agent.clearance_ceiling == "INTERNAL"


def test_acl_filtering_is_measured_and_reported(
    db: Session, tenant_id: str, organization_id: str, user_id: str
) -> None:
    ctx = make_ctx(tenant_id, organization_id, clearance="INTERNAL", roles={"operator"}, user_id=user_id)
    result = retrieval.search(db, ctx, RESTRICTED_QUERY, top_k=20)
    assert result.candidates_before_acl >= result.candidates_after_acl
    # The RESTRICTED workforce note is in the corpus but must be withheld here.
    assert result.acl_filtered_count >= 1


def test_retrieval_is_recorded_for_audit(
    db: Session, tenant_id: str, organization_id: str, user_id: str
) -> None:
    ctx = make_ctx(tenant_id, organization_id, clearance="INTERNAL", roles={"operator"}, user_id=user_id)
    before = db.execute(text("SELECT count(*) FROM retrieval_queries")).scalar_one()
    retrieval.search(db, ctx, "point machine obsolescence", top_k=3)
    after = db.execute(text("SELECT count(*) FROM retrieval_queries")).scalar_one()
    assert after == before + 1


# ------------------------------------------------------------------ embeddings
@pytest.mark.unit
def test_embeddings_are_deterministic_and_normalised() -> None:
    embedder = DeterministicHashEmbedder(384)
    a = embedder.embed_one("escalator step chain tensioner")
    b = embedder.embed_one("escalator step chain tensioner")
    assert a == b
    assert abs(sum(x * x for x in a) ** 0.5 - 1.0) < 1e-9


@pytest.mark.unit
def test_related_text_scores_above_unrelated_text() -> None:
    embedder = DeterministicHashEmbedder(384)
    anchor = embedder.embed_one("escalator step chain tensioner failure")
    related = embedder.embed_one("the escalator step chain tensioner failed again")
    unrelated = embedder.embed_one("quarterly advertising revenue increased")
    assert cosine(anchor, related) > cosine(anchor, unrelated) + 0.2


# ----------------------------------------------------------------- citations
@pytest.mark.unit
def test_citation_verification_rejects_unsupported_claims() -> None:
    sources = [
        {"id": "s1", "text": "Step chain elongation is the dominant escalator failure mode."},
        {"id": "s2", "text": "Fleet availability must not fall below 97 percent."},
    ]
    result = retrieval.verify_citations(
        [
            {"statement": "Step chain elongation is the dominant failure mode", "citations": ["s1"]},
            {"statement": "The network carried 12 million passengers", "citations": ["s2"]},
            {"statement": "Something entirely unsupported", "citations": []},
            {"statement": "Availability floor is 97 percent", "citations": ["s-missing"]},
        ],
        sources,
        min_overlap=0.5,
    )
    verified = {v["statement"] for v in result["verified"]}
    assert "Step chain elongation is the dominant failure mode" in verified
    assert len(result["unverified"]) == 3
    reasons = " ".join(u["reason"] for u in result["unverified"])
    assert "no citation" in reasons
    assert "cited source not found" in reasons
    assert 0.0 < result["coverage"] < 1.0


def test_an_unrecognised_agent_ceiling_cannot_widen_access() -> None:
    """A bogus ceiling must clamp to PUBLIC, not outrank RESTRICTED.

    `classification_rank` deliberately ranks unknown values above every real
    classification, so that an unrecognised *document* classification is treated
    as maximally sensitive. Applied to a *ceiling* that arithmetic inverts: with
    no human in context, an unrecognised ceiling would be the only candidate and
    would outrank every document, admitting all of them.

    The tool gateway validates this parameter against an enum before dispatch,
    so nothing reaches it today. This asserts the floor underneath that check.
    """
    from agentic_os.core.context import ExecutionContext, classification_rank
    from agentic_os.knowledge.retrieval import effective_clearance

    agentless = ExecutionContext(tenant_id="t", organization_id="o")
    assert agentless.human is None

    for bogus in ("SUPERSECRET", "restricted", "", "PUBLIC ", "TOP_SECRET"):
        resolved = effective_clearance(agentless, bogus)
        assert resolved == "PUBLIC", f"{bogus!r} resolved to {resolved!r}"
        assert classification_rank(resolved) == 0

    # Real values are still honoured exactly.
    for good in ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"):
        assert effective_clearance(agentless, good) == good
