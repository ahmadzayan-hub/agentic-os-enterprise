"""Built-in tool implementations.

These execute inside the platform boundary — they touch the platform's own
governed data, never a third-party system — so they are the tools that work
with no external configuration. Each one receives an already-authorised context
from the gateway; none of them re-implements authorization, but every one that
reads tenant data does so through a tenant-bound session, so RLS still applies.
"""

from __future__ import annotations

import ast
import json
import math
import operator
import statistics
from datetime import timedelta
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.context import ExecutionContext
from agentic_os.core.errors import NotFound, ValidationError
from agentic_os.core.ids import utcnow
from agentic_os.knowledge import graph, retrieval

# ---------------------------------------------------------------------------
# calc.evaluate — sandboxed arithmetic
# ---------------------------------------------------------------------------
_BINARY_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sum": sum,
    "len": len,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "mean": statistics.fmean,
    "median": statistics.median,
    "stdev": lambda xs: statistics.pstdev(xs) if len(xs) > 1 else 0.0,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}

#: Guards against expression bombs such as ``9**9**9``.
_MAX_EXPONENT = 1024
_MAX_NODES = 400


def _eval_node(node: ast.AST, variables: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, variables)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool)):
            return node.value
        raise ValidationError(f"unsupported literal type: {type(node.value).__name__}")
    if isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise ValidationError(f"unknown variable '{node.id}'")
    if isinstance(node, ast.BinOp):
        op = _BINARY_OPS.get(type(node.op))
        if op is None:
            raise ValidationError(f"unsupported operator: {type(node.op).__name__}")
        left, right = _eval_node(node.left, variables), _eval_node(node.right, variables)
        if isinstance(node.op, ast.Pow) and abs(float(right)) > _MAX_EXPONENT:
            raise ValidationError(f"exponent exceeds the limit of {_MAX_EXPONENT}")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValidationError(f"unsupported unary operator: {type(node.op).__name__}")
        return op(_eval_node(node.operand, variables))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ValidationError("only whitelisted mathematical functions may be called")
        args = [_eval_node(a, variables) for a in node.args]
        return _FUNCTIONS[node.func.id](*args)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval_node(e, variables) for e in node.elts]
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, variables)
        for op_node, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval_node(comparator, variables)
            comparison = {
                ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
                ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
            }.get(type(op_node))
            if comparison is None or not comparison(left, right):
                return False
            left = right
        return True
    raise ValidationError(f"unsupported expression element: {type(node).__name__}")


def calc_evaluate(
    session: Session, ctx: ExecutionContext, params: dict[str, Any]
) -> dict[str, Any]:
    """Evaluate an arithmetic expression with no access to Python internals."""
    expression = str(params["expression"])
    variables = dict(params.get("variables") or {})
    precision = int(params.get("precision", 6))

    for name, value in variables.items():
        if not isinstance(value, (int, float, list)):
            raise ValidationError(f"variable '{name}' must be numeric or a list of numbers")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValidationError(f"invalid expression: {exc.msg}") from exc

    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > _MAX_NODES:
        raise ValidationError(f"expression is too complex ({node_count} nodes)")

    try:
        value = _eval_node(tree, variables)
    except ZeroDivisionError as exc:
        raise ValidationError("division by zero") from exc
    except (OverflowError, ValueError) as exc:
        raise ValidationError(f"arithmetic error: {exc}") from exc

    if isinstance(value, bool):
        result: Any = value
    elif isinstance(value, (int, float)):
        result = round(float(value), precision)
    else:
        result = value

    return {
        "value": result,
        "expression": expression,
        "variables": variables,
        "deterministic": True,
        "evaluator": "ast_sandbox_v1",
    }


# ---------------------------------------------------------------------------
# knowledge.*
# ---------------------------------------------------------------------------
def knowledge_search(
    session: Session, ctx: ExecutionContext, params: dict[str, Any]
) -> dict[str, Any]:
    result = retrieval.search(
        session,
        ctx,
        str(params["query"]),
        top_k=int(params.get("top_k", 8)),
        strategy=params.get("strategy", "hybrid"),
        agent_clearance_ceiling=params.get("classification_ceiling"),
    )
    return result.to_dict()


def knowledge_fetch_document(
    session: Session, ctx: ExecutionContext, params: dict[str, Any]
) -> dict[str, Any]:
    return retrieval.fetch_document(
        session,
        ctx,
        str(params["document_id"]),
        max_chars=int(params.get("max_chars", 20000)),
    )


# ---------------------------------------------------------------------------
# graph.*
# ---------------------------------------------------------------------------
def graph_query(session: Session, ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    return graph.query(
        session,
        ctx,
        node_key=str(params.get("node_key", "")),
        node_type=str(params.get("node_type", "")),
        relation=str(params.get("relation", "")),
        depth=int(params.get("depth", 1)),
        limit=int(params.get("limit", 50)),
    )


def graph_impact_analysis(
    session: Session, ctx: ExecutionContext, params: dict[str, Any]
) -> dict[str, Any]:
    return graph.impact_analysis(
        session,
        ctx,
        node_key=str(params["node_key"]),
        depth=int(params.get("depth", 3)),
        direction=str(params.get("direction", "downstream")),
    )


# ---------------------------------------------------------------------------
# dataset.*
# ---------------------------------------------------------------------------
_FILTER_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: _num(a) > _num(b),
    "gte": lambda a, b: _num(a) >= _num(b),
    "lt": lambda a, b: _num(a) < _num(b),
    "lte": lambda a, b: _num(a) <= _num(b),
    "contains": lambda a, b: str(b).lower() in str(a).lower(),
    "in": lambda a, b: a in (b if isinstance(b, list) else [b]),
}


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _load_dataset(session: Session, ctx: ExecutionContext, dataset_key: str) -> dict[str, Any]:
    row = session.execute(
        text(
            "SELECT id, dataset_key, name, schema_fields, row_count, quality_score, "
            "quality_detail, classification, freshness_at, source_system "
            "FROM datasets WHERE tenant_id = :t AND dataset_key = :k AND status = 'ACTIVE'"
        ),
        {"t": ctx.tenant_id, "k": dataset_key},
    ).mappings().first()
    if row is None:
        raise NotFound(f"dataset '{dataset_key}' not found")
    return dict(row)


def dataset_query(
    session: Session, ctx: ExecutionContext, params: dict[str, Any]
) -> dict[str, Any]:
    """Deterministic filter/aggregate over a governed dataset, with lineage."""
    dataset = _load_dataset(session, ctx, str(params["dataset_key"]))
    rows = session.execute(
        text(
            """
            SELECT r.data, r.row_key, r.quality_flags, b.source_file, b.ingested_at
            FROM dataset_rows r
            JOIN dataset_batches b ON b.id = r.batch_id
            WHERE r.tenant_id = :t AND r.dataset_id = :d
            """
        ),
        {"t": ctx.tenant_id, "d": dataset["id"]},
    ).mappings().all()

    records = [
        {
            **(r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])),
            "_row_key": r["row_key"],
            "_source_file": r["source_file"],
            "_quality_flags": list(r["quality_flags"] or []),
        }
        for r in rows
    ]

    for spec in params.get("filters") or []:
        op = _FILTER_OPS.get(spec["op"])
        if op is None:
            raise ValidationError(f"unsupported filter operator '{spec['op']}'")
        field_name, value = spec["field"], spec["value"]
        records = [r for r in records if field_name in r and op(r[field_name], value)]

    aggregate = params.get("aggregate")
    group_by = params.get("group_by")

    if aggregate:
        agg_field = params.get("aggregate_field")
        if aggregate != "count" and not agg_field:
            raise ValidationError(f"aggregate '{aggregate}' requires aggregate_field")

        def apply(values: list[Any]) -> Any:
            if aggregate == "count":
                return len(values)
            numbers = [_num(v) for v in values if not math.isnan(_num(v))]
            if not numbers:
                return None
            return {
                "sum": sum,
                "avg": lambda xs: round(statistics.fmean(xs), 6),
                "min": min,
                "max": max,
            }[aggregate](numbers)

        if group_by:
            groups: dict[Any, list[Any]] = {}
            for record in records:
                key = record.get(group_by)
                groups.setdefault(key, []).append(
                    record.get(agg_field) if agg_field else 1
                )
            result = [
                {group_by: key, aggregate: apply(values), "row_count": len(values)}
                for key, values in groups.items()
            ]
            result.sort(key=lambda r: (r[aggregate] is None, r[aggregate]), reverse=True)
        else:
            values = [r.get(agg_field) if agg_field else 1 for r in records]
            result = [{aggregate: apply(values), "row_count": len(records)}]

        return {
            "dataset_key": dataset["dataset_key"],
            "aggregate": aggregate,
            "group_by": group_by,
            "results": result[: int(params.get("limit", 100))],
            "matched_rows": len(records),
            "lineage": {
                "source_system": dataset["source_system"],
                "dataset_row_count": dataset["row_count"],
                "freshness_at": dataset["freshness_at"],
                "quality_score": (
                    float(dataset["quality_score"]) if dataset["quality_score"] else None
                ),
            },
            "deterministic": True,
        }

    order_by = params.get("order_by")
    if order_by:
        records.sort(
            key=lambda r: (r.get(order_by) is None, _num(r.get(order_by))),
            reverse=bool(params.get("descending")),
        )

    limit = int(params.get("limit", 100))
    return {
        "dataset_key": dataset["dataset_key"],
        "rows": records[:limit],
        "matched_rows": len(records),
        "returned_rows": min(limit, len(records)),
        "truncated": len(records) > limit,
        "lineage": {
            "source_system": dataset["source_system"],
            "dataset_row_count": dataset["row_count"],
            "freshness_at": dataset["freshness_at"],
            "quality_score": float(dataset["quality_score"]) if dataset["quality_score"] else None,
        },
        "deterministic": True,
    }


def dataset_profile(
    session: Session, ctx: ExecutionContext, params: dict[str, Any]
) -> dict[str, Any]:
    """Score completeness, validity, uniqueness, consistency and freshness."""
    dataset = _load_dataset(session, ctx, str(params["dataset_key"]))
    rows = session.execute(
        text("SELECT data FROM dataset_rows WHERE tenant_id = :t AND dataset_id = :d"),
        {"t": ctx.tenant_id, "d": dataset["id"]},
    ).scalars().all()
    records = [r if isinstance(r, dict) else json.loads(r) for r in rows]

    if not records:
        return {
            "dataset_key": dataset["dataset_key"],
            "row_count": 0,
            "overall_score": 0.0,
            "dimensions": {},
            "fields": {},
            "note": "dataset contains no rows; quality cannot be assessed",
        }

    fields = sorted({k for r in records for k in r})
    total = len(records)
    field_report: dict[str, dict[str, Any]] = {}

    for name in fields:
        values = [r.get(name) for r in records]
        populated = [v for v in values if v not in (None, "", [])]
        completeness = len(populated) / total
        distinct = len({str(v) for v in populated})
        uniqueness = distinct / len(populated) if populated else 0.0
        numeric = [v for v in populated if not math.isnan(_num(v))]
        type_consistency = (
            max(len(numeric), len(populated) - len(numeric)) / len(populated) if populated else 0.0
        )
        field_report[name] = {
            "completeness": round(completeness, 4),
            "uniqueness": round(uniqueness, 4),
            "type_consistency": round(type_consistency, 4),
            "populated": len(populated),
            "distinct": distinct,
            "inferred_type": "numeric" if len(numeric) > len(populated) / 2 else "text",
        }

    primary_key = dataset.get("primary_key_field") or ""
    if primary_key and primary_key in fields:
        keys = [str(r.get(primary_key)) for r in records if r.get(primary_key) is not None]
        key_uniqueness = len(set(keys)) / len(keys) if keys else 0.0
    else:
        key_uniqueness = 1.0

    freshness_at = dataset["freshness_at"]
    if freshness_at is None:
        freshness = 0.5
        freshness_detail = "no freshness timestamp recorded"
    else:
        age_days = (utcnow() - freshness_at) / timedelta(days=1)
        freshness = max(0.0, min(1.0, 1 - age_days / 90))
        freshness_detail = f"{age_days:.1f} days old"

    completeness = round(statistics.fmean(f["completeness"] for f in field_report.values()), 4)
    consistency = round(statistics.fmean(f["type_consistency"] for f in field_report.values()), 4)
    dimensions = {
        "completeness": completeness,
        "validity": consistency,
        "uniqueness": round(key_uniqueness, 4),
        "consistency": consistency,
        "freshness": round(freshness, 4),
    }
    overall = round(statistics.fmean(dimensions.values()), 4)

    session.execute(
        text(
            "UPDATE datasets SET quality_score = :s, quality_detail = CAST(:d AS jsonb), "
            "updated_at = now() WHERE id = :i"
        ),
        {
            "s": overall,
            "d": json.dumps({"dimensions": dimensions, "fields": field_report}, default=str),
            "i": dataset["id"],
        },
    )

    weakest = sorted(dimensions.items(), key=lambda kv: kv[1])[:2]
    return {
        "dataset_key": dataset["dataset_key"],
        "row_count": total,
        "field_count": len(fields),
        "overall_score": overall,
        "dimensions": dimensions,
        "fields": field_report,
        "freshness_detail": freshness_detail,
        "weakest_dimensions": [{"dimension": k, "score": v} for k, v in weakest],
        "deterministic": True,
    }


# ---------------------------------------------------------------------------
# analytics.query_metrics
# ---------------------------------------------------------------------------
def analytics_query_metrics(
    session: Session, ctx: ExecutionContext, params: dict[str, Any]
) -> dict[str, Any]:
    metric = str(params["metric"])
    window_hours = int(params.get("window_hours", 24))
    aggregate = str(params.get("aggregate", "sum"))
    function = {"sum": "sum", "avg": "avg", "min": "min", "max": "max", "count": "count"}[aggregate]

    row = session.execute(
        text(
            f"""
            SELECT {function}(value) AS result, count(*) AS samples,
                   min(recorded_at) AS first_sample, max(recorded_at) AS last_sample
            FROM metric_samples
            WHERE tenant_id = :t AND metric = :m
              AND recorded_at >= now() - make_interval(hours => :hours)
            """
        ),
        {"t": ctx.tenant_id, "m": metric, "hours": window_hours},
    ).mappings().one()

    return {
        "metric": metric,
        "aggregate": aggregate,
        "window_hours": window_hours,
        "value": float(row["result"]) if row["result"] is not None else None,
        "samples": int(row["samples"]),
        "first_sample": row["first_sample"],
        "last_sample": row["last_sample"],
        "deterministic": True,
    }


# ---------------------------------------------------------------------------
# tasks.create
# ---------------------------------------------------------------------------
def tasks_create(session: Session, ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    assignee_id = None
    if email := params.get("assignee_email"):
        row = session.execute(
            text("SELECT id FROM users WHERE tenant_id = :t AND email = :e"),
            {"t": ctx.tenant_id, "e": str(email).lower()},
        ).first()
        if row is None:
            raise NotFound(f"no user with email '{email}' in this tenant")
        assignee_id = row.id

    row = session.execute(
        text(
            """
            INSERT INTO tasks (tenant_id, run_id, title, description, assignee_user_id,
                               assignee_agent_key, priority, due_at)
            VALUES (:t, :run, :title, :desc, :assignee, :agent, :priority,
                    CAST(NULLIF(:due, '') AS timestamptz))
            RETURNING id, title, status, priority, created_at
            """
        ),
        {
            "t": ctx.tenant_id,
            "run": ctx.run_id or None,
            "title": str(params["title"]),
            "desc": str(params.get("description", "")),
            "assignee": assignee_id,
            "agent": ctx.agent.agent_id if ctx.agent else "",
            "priority": str(params.get("priority", "MEDIUM")),
            "due": str(params.get("due_at", "")),
        },
    ).mappings().one()
    return {"task_id": str(row["id"]), **{k: v for k, v in row.items() if k != "id"}}


#: Only tools registered here can execute. A registry entry without an
#: implementation resolves to NOT_IMPLEMENTED at the gateway.
BUILTIN_TOOLS: dict[str, Callable[[Session, ExecutionContext, dict[str, Any]], dict[str, Any]]] = {
    "calc.evaluate": calc_evaluate,
    "knowledge.search": knowledge_search,
    "knowledge.fetch_document": knowledge_fetch_document,
    "graph.query": graph_query,
    "graph.impact_analysis": graph_impact_analysis,
    "dataset.query": dataset_query,
    "dataset.profile": dataset_profile,
    "analytics.query_metrics": analytics_query_metrics,
    "tasks.create": tasks_create,
}
