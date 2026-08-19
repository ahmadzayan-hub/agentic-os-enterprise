"""Tracing and metrics.

Spans and metric samples are persisted in the platform's own tables so that a
run's trace is queryable without an external backend, and are simultaneously
emitted to OpenTelemetry when an OTLP endpoint is configured. The database is
the system of record for governance; OTel is for operational tooling.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.config import get_settings
from agentic_os.core.context import ExecutionContext
from agentic_os.core.ids import new_ulid, utcnow


@dataclass(slots=True)
class Span:
    trace_id: str
    span_id: str
    name: str
    parent_span_id: str = ""
    kind: str = "INTERNAL"
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"


@contextlib.contextmanager
def span(
    session: Session,
    ctx: ExecutionContext,
    name: str,
    *,
    kind: str = "INTERNAL",
    parent_span_id: str = "",
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span]:
    """Record one span for the duration of the block."""
    trace_id = ctx.trace_id or new_ulid()
    current = Span(
        trace_id=trace_id,
        span_id=new_ulid(),
        name=name,
        parent_span_id=parent_span_id,
        kind=kind,
        attributes=dict(attributes or {}),
    )
    started_at = utcnow()
    started = time.perf_counter()
    try:
        yield current
    except Exception as exc:
        current.status = "ERROR"
        current.attributes["error"] = str(exc)[:500]
        raise
    finally:
        duration_ms = int((time.perf_counter() - started) * 1000)
        session.execute(
            text(
                """
                INSERT INTO traces (tenant_id, trace_id, span_id, parent_span_id, name, kind,
                                    run_id, attributes, status, started_at, ended_at, duration_ms)
                VALUES (:t, :trace, :span, :parent, :name, :kind, :run,
                        CAST(:attrs AS jsonb), :status, :started, now(), :duration)
                ON CONFLICT (trace_id, span_id) DO NOTHING
                """
            ),
            {
                "t": ctx.tenant_id,
                "trace": current.trace_id,
                "span": current.span_id,
                "parent": current.parent_span_id,
                "name": current.name,
                "kind": current.kind,
                "run": ctx.run_id or None,
                "attrs": json.dumps(current.attributes, default=str),
                "status": current.status,
                "started": started_at,
                "duration": duration_ms,
            },
        )


def record_metric(
    session: Session,
    ctx: ExecutionContext,
    metric: str,
    value: float,
    *,
    labels: dict[str, Any] | None = None,
) -> None:
    session.execute(
        text(
            "INSERT INTO metric_samples (tenant_id, metric, labels, value) "
            "VALUES (:t, :m, CAST(:l AS jsonb), :v)"
        ),
        {
            "t": ctx.tenant_id,
            "m": metric,
            "l": json.dumps(labels or {}, default=str),
            "v": float(value),
        },
    )


def run_trace(session: Session, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            "SELECT trace_id, span_id, parent_span_id, name, kind, status, attributes, "
            "started_at, duration_ms FROM traces "
            "WHERE tenant_id = :t AND run_id = CAST(:r AS uuid) ORDER BY started_at"
        ),
        {"t": tenant_id, "r": run_id},
    ).mappings()
    return [dict(r) for r in rows]


def platform_metrics(session: Session, tenant_id: str, *, window_hours: int = 24) -> dict[str, Any]:
    """Operational metrics computed from recorded activity."""
    runs = (
        session.execute(
            text(
                """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status = 'SUCCEEDED') AS succeeded,
                   count(*) FILTER (WHERE status = 'FAILED') AS failed,
                   COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms), 0) AS p95_ms
            FROM runs
            WHERE tenant_id = :t AND created_at >= now() - make_interval(hours => :h)
            """
            ),
            {"t": tenant_id, "h": window_hours},
        )
        .mappings()
        .one()
    )

    tools = (
        session.execute(
            text(
                """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE gateway_decision = 'DENIED') AS denied,
                   count(*) FILTER (WHERE verification_status = 'FAILED') AS verification_failed,
                   COALESCE(avg(latency_ms), 0) AS avg_latency_ms
            FROM tool_calls
            WHERE tenant_id = :t AND created_at >= now() - make_interval(hours => :h)
            """
            ),
            {"t": tenant_id, "h": window_hours},
        )
        .mappings()
        .one()
    )

    retrieval = (
        session.execute(
            text(
                """
            SELECT count(*) AS queries, COALESCE(avg(latency_ms), 0) AS avg_latency_ms,
                   COALESCE(sum(candidates_before_acl - candidates_after_acl), 0) AS acl_filtered
            FROM retrieval_queries
            WHERE tenant_id = :t AND created_at >= now() - make_interval(hours => :h)
            """
            ),
            {"t": tenant_id, "h": window_hours},
        )
        .mappings()
        .one()
    )

    security = (
        session.execute(
            text(
                """
            SELECT count(*) AS findings,
                   count(*) FILTER (WHERE severity IN ('HIGH', 'CRITICAL')) AS severe
            FROM security_findings
            WHERE tenant_id = :t AND created_at >= now() - make_interval(hours => :h)
            """
            ),
            {"t": tenant_id, "h": window_hours},
        )
        .mappings()
        .one()
    )

    policy = (
        session.execute(
            text(
                """
            SELECT count(*) AS decisions,
                   count(*) FILTER (WHERE effect = 'DENY') AS denied,
                   count(*) FILTER (WHERE effect = 'REQUIRE_APPROVAL') AS escalated
            FROM policy_decisions
            WHERE tenant_id = :t AND evaluated_at >= now() - make_interval(hours => :h)
            """
            ),
            {"t": tenant_id, "h": window_hours},
        )
        .mappings()
        .one()
    )

    total_runs = int(runs["total"])
    total_tools = int(tools["total"])
    return {
        "window_hours": window_hours,
        "runs": {
            "total": total_runs,
            "succeeded": int(runs["succeeded"]),
            "failed": int(runs["failed"]),
            "success_rate": round(int(runs["succeeded"]) / total_runs, 4) if total_runs else None,
            "p95_duration_ms": int(runs["p95_ms"]),
        },
        "tools": {
            "total": total_tools,
            "denied": int(tools["denied"]),
            "denial_rate": round(int(tools["denied"]) / total_tools, 4) if total_tools else None,
            "verification_failed": int(tools["verification_failed"]),
            "avg_latency_ms": int(tools["avg_latency_ms"]),
        },
        "retrieval": {
            "queries": int(retrieval["queries"]),
            "avg_latency_ms": int(retrieval["avg_latency_ms"]),
            "chunks_withheld_by_acl": int(retrieval["acl_filtered"]),
        },
        "security": {
            "findings": int(security["findings"]),
            "severe_findings": int(security["severe"]),
        },
        "policy": {
            "decisions": int(policy["decisions"]),
            "denied": int(policy["denied"]),
            "escalated_to_approval": int(policy["escalated"]),
        },
        "otel_endpoint_configured": bool(get_settings().otel_exporter_otlp_endpoint),
    }
