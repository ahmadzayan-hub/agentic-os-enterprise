"""G-Brain: the enterprise intelligence graph.

Nodes and edges are extracted from governed content and platform state, always
with a source reference and a confidence, so every relationship can be traced
back to the document or record that asserted it.

Extraction is pattern-based and conservative. It recognises the identifier
conventions the platform already knows (work orders, assets, documents,
invoices, projects) rather than attempting open-domain entity extraction, which
would produce a graph nobody can trust. Unrecognised entities are simply not
asserted.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext, classification_rank
from agentic_os.core.db import affected_rows
from agentic_os.core.errors import NotFound

#: Identifier conventions the extractor recognises, mapped to node types.
_ENTITY_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("ASSET", "AST", re.compile(r"\b(?:AST|ASSET)[- ]?(\d{3,})\b", re.IGNORECASE)),
    ("PROCESS", "WO", re.compile(r"\bWO[- ]?(\d{3,})\b", re.IGNORECASE)),
    ("DOCUMENT", "DOC", re.compile(r"\bDOC[- ]?(\d{3,})\b", re.IGNORECASE)),
    ("CONTRACT", "CTR", re.compile(r"\b(?:CTR|CONTRACT)[- ]?(\d{3,})\b", re.IGNORECASE)),
    ("PROJECT", "PRJ", re.compile(r"\b(?:PRJ|PROJECT)[- ]?(\d{3,})\b", re.IGNORECASE)),
    ("RISK", "RSK", re.compile(r"\b(?:RSK|RISK)[- ]?(\d{3,})\b", re.IGNORECASE)),
    ("APPLICATION", "APP", re.compile(r"\b(?:APP|SYSTEM)[- ]?(\d{3,})\b", re.IGNORECASE)),
)

#: Relationship verbs that connect two identifiers in the same sentence.
_RELATION_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("depends_on", ("depends on", "requires", "relies on", "is dependent on")),
    ("affects", ("affects", "impacts", "influences")),
    ("caused_by", ("caused by", "due to", "resulting from", "attributed to")),
    ("mitigates", ("mitigates", "reduces", "controls", "addresses")),
    ("owns", ("owns", "is owned by", "responsible for")),
    ("contains", ("contains", "includes", "comprises")),
    ("uses", ("uses", "utilises", "utilizes", "operates")),
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(slots=True)
class GraphNode:
    node_key: str
    node_type: str
    label: str
    properties: dict[str, Any] = field(default_factory=dict)
    classification: str = "INTERNAL"
    confidence: float = 1.0
    source_ref: str = ""


@dataclass(slots=True)
class GraphEdge:
    from_key: str
    to_key: str
    relation: str
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    source_ref: str = ""


def upsert_node(
    session: Session, ctx: ExecutionContext, node: GraphNode, *, document_id: str | None = None
) -> str:
    row = session.execute(
        text(
            """
            INSERT INTO knowledge_nodes (tenant_id, node_key, node_type, label, properties,
                                         classification, source_ref, document_id, confidence)
            VALUES (:t, :k, :ty, :l, CAST(:p AS jsonb), CAST(:c AS data_classification),
                    :src, :doc, :conf)
            ON CONFLICT (tenant_id, node_key) DO UPDATE
              SET label = EXCLUDED.label,
                  properties = knowledge_nodes.properties || EXCLUDED.properties,
                  confidence = GREATEST(knowledge_nodes.confidence, EXCLUDED.confidence)
            RETURNING id
            """
        ),
        {
            "t": ctx.tenant_id,
            "k": node.node_key,
            "ty": node.node_type,
            "l": node.label,
            "p": json.dumps(node.properties, default=str),
            "c": node.classification,
            "src": node.source_ref,
            "doc": document_id,
            "conf": node.confidence,
        },
    ).one()
    return str(row.id)


def upsert_edge(session: Session, ctx: ExecutionContext, edge: GraphEdge) -> bool:
    """Create an edge between two existing nodes. Returns False if either is missing."""
    if edge.from_key == edge.to_key:
        return False
    result = session.execute(
        text(
            """
            INSERT INTO knowledge_edges (tenant_id, from_node_id, to_node_id, relation,
                                         properties, confidence, source_ref)
            SELECT :t, f.id, tn.id, :rel, CAST(:p AS jsonb), :conf, :src
            FROM knowledge_nodes f, knowledge_nodes tn
            WHERE f.tenant_id = :t AND f.node_key = :from_key
              AND tn.tenant_id = :t AND tn.node_key = :to_key
            ON CONFLICT (from_node_id, to_node_id, relation) DO UPDATE
              SET confidence = GREATEST(knowledge_edges.confidence, EXCLUDED.confidence)
            """
        ),
        {
            "t": ctx.tenant_id,
            "from_key": edge.from_key,
            "to_key": edge.to_key,
            "rel": edge.relation,
            "p": json.dumps(edge.properties, default=str),
            "conf": edge.confidence,
            "src": edge.source_ref,
        },
    )
    return affected_rows(result) > 0


def extract_entities(content: str) -> list[GraphNode]:
    """Recognise known identifier conventions. Conservative by design."""
    nodes: dict[str, GraphNode] = {}
    for node_type, prefix, pattern in _ENTITY_PATTERNS:
        for match in pattern.finditer(content):
            number = match.group(1)
            key = f"{prefix}-{number}"
            nodes.setdefault(
                key,
                GraphNode(
                    node_key=key,
                    node_type=node_type,
                    label=key,
                    properties={"identifier": key, "extracted": True},
                    confidence=0.85,
                ),
            )
    return list(nodes.values())


def extract_relations(content: str, known_keys: set[str]) -> list[GraphEdge]:
    """Connect identifiers that co-occur with a relationship verb between them."""
    edges: list[GraphEdge] = []
    for sentence in _SENTENCE.split(content):
        found: list[tuple[int, str]] = []
        for _, prefix, pattern in _ENTITY_PATTERNS:
            for match in pattern.finditer(sentence):
                key = f"{prefix}-{match.group(1)}"
                if key in known_keys:
                    found.append((match.start(), key))
        if len(found) < 2:
            continue
        found.sort()
        lowered = sentence.lower()
        relation = "related_to"
        confidence = 0.5
        for candidate, hints in _RELATION_HINTS:
            if any(hint in lowered for hint in hints):
                relation, confidence = candidate, 0.75
                break
        for (_left_pos, left), (_right_pos, right) in zip(found, found[1:], strict=False):
            if left == right:
                continue
            edges.append(
                GraphEdge(
                    from_key=left,
                    to_key=right,
                    relation=relation,
                    confidence=confidence,
                    properties={"sentence": sentence.strip()[:300]},
                )
            )
    return edges


def extract_from_document(
    session: Session, ctx: ExecutionContext, *, document_id: str, title: str, content: str
) -> tuple[int, int]:
    """Extract entities and relations from a document. Returns (nodes, edges)."""
    document_key = f"DOCUMENT:{document_id}"
    upsert_node(
        session,
        ctx,
        GraphNode(
            node_key=document_key,
            node_type="DOCUMENT",
            label=title[:200],
            properties={"document_id": document_id},
            source_ref=f"document:{document_id}",
        ),
        document_id=document_id,
    )

    entities = extract_entities(content)
    for node in entities:
        node.source_ref = f"document:{document_id}"
        upsert_node(session, ctx, node, document_id=document_id)
        upsert_edge(
            session,
            ctx,
            GraphEdge(
                from_key=document_key,
                to_key=node.node_key,
                relation="contains",
                confidence=0.9,
                source_ref=f"document:{document_id}",
            ),
        )

    known = {n.node_key for n in entities}
    edge_count = 0
    for edge in extract_relations(content, known):
        edge.source_ref = f"document:{document_id}"
        if upsert_edge(session, ctx, edge):
            edge_count += 1

    return len(entities) + 1, edge_count + len(entities)


# ---------------------------------------------------------------------------
# Query surface
# ---------------------------------------------------------------------------
def _clearance_rank(ctx: ExecutionContext) -> int:
    return classification_rank(ctx.human.clearance if ctx.human else "PUBLIC")


def query(
    session: Session,
    ctx: ExecutionContext,
    *,
    node_key: str = "",
    node_type: str = "",
    relation: str = "",
    depth: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """Query the graph, honouring the caller's clearance."""
    rank = _clearance_rank(ctx)
    if node_key:
        return traverse(session, ctx, node_key=node_key, depth=depth, relation=relation, limit=limit)

    rows = (
        session.execute(
            text(
                """
            SELECT node_key, node_type, label, properties, classification, confidence, source_ref
            FROM knowledge_nodes
            WHERE tenant_id = :t
              AND (:ntype = '' OR node_type = :ntype)
              AND (valid_to IS NULL OR valid_to > now())
              AND CASE classification
                    WHEN 'PUBLIC' THEN 0 WHEN 'INTERNAL' THEN 1
                    WHEN 'CONFIDENTIAL' THEN 2 ELSE 3 END <= :rank
            ORDER BY label
            LIMIT :limit
            """
            ),
            {"t": ctx.tenant_id, "ntype": node_type, "rank": rank, "limit": limit},
        )
        .mappings()
        .all()
    )
    return {"nodes": [dict(r) for r in rows], "edges": [], "depth": 0}


def traverse(
    session: Session,
    ctx: ExecutionContext,
    *,
    node_key: str,
    depth: int = 2,
    direction: str = "both",
    relation: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """Breadth-first traversal from a node, bounded by depth and clearance."""
    rank = _clearance_rank(ctx)
    root = (
        session.execute(
            text(
                """
            SELECT node_key, node_type, label, properties, classification
            FROM knowledge_nodes
            WHERE tenant_id = :t AND node_key = :k
              AND CASE classification
                    WHEN 'PUBLIC' THEN 0 WHEN 'INTERNAL' THEN 1
                    WHEN 'CONFIDENTIAL' THEN 2 ELSE 3 END <= :rank
            """
            ),
            {"t": ctx.tenant_id, "k": node_key, "rank": rank},
        )
        .mappings()
        .first()
    )
    if root is None:
        raise NotFound(f"graph node '{node_key}' not found or not accessible")

    seen: dict[str, dict] = {node_key: dict(root)}
    edges: list[dict] = []
    frontier: deque[tuple[str, int]] = deque([(node_key, 0)])

    while frontier and len(seen) < limit:
        current, level = frontier.popleft()
        if level >= depth:
            continue
        rows = (
            session.execute(
                text(
                    """
                SELECT e.relation, e.confidence, e.properties,
                       fn.node_key AS from_key, tn.node_key AS to_key,
                       tn.node_type AS to_type, tn.label AS to_label,
                       tn.classification AS to_class,
                       fn.node_type AS from_type, fn.label AS from_label,
                       fn.classification AS from_class
                FROM knowledge_edges e
                JOIN knowledge_nodes fn ON fn.id = e.from_node_id
                JOIN knowledge_nodes tn ON tn.id = e.to_node_id
                WHERE e.tenant_id = :t
                  AND (:rel = '' OR e.relation = :rel)
                  AND (
                    (:dir IN ('both', 'downstream') AND fn.node_key = :k)
                    OR (:dir IN ('both', 'upstream') AND tn.node_key = :k)
                  )
                  AND CASE tn.classification
                        WHEN 'PUBLIC' THEN 0 WHEN 'INTERNAL' THEN 1
                        WHEN 'CONFIDENTIAL' THEN 2 ELSE 3 END <= :rank
                  AND CASE fn.classification
                        WHEN 'PUBLIC' THEN 0 WHEN 'INTERNAL' THEN 1
                        WHEN 'CONFIDENTIAL' THEN 2 ELSE 3 END <= :rank
                LIMIT :limit
                """
                ),
                {
                    "t": ctx.tenant_id,
                    "k": current,
                    "rel": relation,
                    "dir": direction,
                    "rank": rank,
                    "limit": limit,
                },
            )
            .mappings()
            .all()
        )

        for row in rows:
            edges.append(
                {
                    "from": row["from_key"],
                    "to": row["to_key"],
                    "relation": row["relation"],
                    "confidence": float(row["confidence"]),
                }
            )
            for key, ntype, label, cls in (
                (row["from_key"], row["from_type"], row["from_label"], row["from_class"]),
                (row["to_key"], row["to_type"], row["to_label"], row["to_class"]),
            ):
                if key not in seen:
                    seen[key] = {
                        "node_key": key,
                        "node_type": ntype,
                        "label": label,
                        "classification": str(cls),
                    }
                    frontier.append((key, level + 1))

    # Deduplicate edges that both directions surfaced.
    unique = {(e["from"], e["to"], e["relation"]): e for e in edges}
    return {
        "root": node_key,
        "depth": depth,
        "nodes": list(seen.values()),
        "edges": list(unique.values()),
        "node_count": len(seen),
        "edge_count": len(unique),
    }


def impact_analysis(
    session: Session,
    ctx: ExecutionContext,
    *,
    node_key: str,
    depth: int = 3,
    direction: str = "downstream",
) -> dict[str, Any]:
    """What a change to this node could affect, ranked by proximity."""
    result = traverse(session, ctx, node_key=node_key, depth=depth, direction=direction, relation="")

    distance: dict[str, int] = {node_key: 0}
    adjacency: dict[str, list[str]] = {}
    for edge in result["edges"]:
        adjacency.setdefault(edge["from"], []).append(edge["to"])
        if direction == "both":
            adjacency.setdefault(edge["to"], []).append(edge["from"])

    frontier: deque[str] = deque([node_key])
    while frontier:
        current = frontier.popleft()
        for neighbour in adjacency.get(current, []):
            if neighbour not in distance:
                distance[neighbour] = distance[current] + 1
                frontier.append(neighbour)

    by_key = {n["node_key"]: n for n in result["nodes"]}
    affected = [
        {**by_key[key], "distance": dist}
        for key, dist in sorted(distance.items(), key=lambda kv: kv[1])
        if key != node_key and key in by_key
    ]
    return {
        "root": node_key,
        "direction": direction,
        "depth": depth,
        "affected_count": len(affected),
        "affected": affected,
        "edges": result["edges"],
        "note": (
            "Impact is derived from asserted graph edges only. Coverage is limited to "
            "relationships the platform has ingested; absence of an edge is not evidence "
            "of independence."
        ),
    }
