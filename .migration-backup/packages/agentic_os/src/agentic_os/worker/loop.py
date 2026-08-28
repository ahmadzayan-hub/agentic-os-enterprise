"""The background worker.

Nothing here holds state. Each pass leases a batch of due workflow runs,
advances each by one step, drains the transactional outbox and expires the
approvals whose window has closed — then commits and starts again. A worker
that dies mid-step loses its lease and another worker resumes from the last
committed step, because the state is in the database rather than in the
process.

Row level security has no bypass predicate, so the worker cannot sweep every
tenant in one query. It enumerates active tenants through the provisioning
role, then binds each tenant in turn and does that tenant's work under its own
policy. That is slightly more work per pass and it is the point: a worker bug
cannot cross a tenant boundary, and one busy tenant cannot starve the others
inside a single scan.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from dataclasses import dataclass, field
from types import FrameType
from typing import Any

from sqlalchemy import text

from agentic_os.control import approval_engine
from agentic_os.core.context import system_context
from agentic_os.core.db import bind_tenant, get_session_factory, provisioning_session_scope
from agentic_os.core.ids import new_ulid
from agentic_os.observability import telemetry
from agentic_os.runtime import events as event_bus
from agentic_os.runtime import workflow_engine

log = logging.getLogger("agentic_os.worker")


@dataclass(slots=True)
class WorkerConfig:
    worker_id: str = field(default_factory=lambda: f"worker-{os.uname().nodename}-{new_ulid()[-8:]}")
    poll_seconds: float = 1.0
    idle_seconds: float = 5.0
    run_batch: int = 10
    outbox_batch: int = 50
    max_passes: int | None = None


@dataclass(slots=True)
class PassResult:
    tenants: int = 0
    runs_advanced: int = 0
    events_dispatched: int = 0
    events_dead: int = 0
    approvals_expired: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def did_work(self) -> bool:
        return bool(self.runs_advanced or self.events_dispatched or self.approvals_expired)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenants": self.tenants,
            "runs_advanced": self.runs_advanced,
            "events_dispatched": self.events_dispatched,
            "events_dead": self.events_dead,
            "approvals_expired": self.approvals_expired,
            "errors": self.errors,
        }


def _active_tenants() -> list[tuple[str, str]]:
    """(tenant_id, organization_id) for every active tenant."""
    with provisioning_session_scope() as session:
        rows = session.execute(
            text("SELECT id, organization_id FROM tenants WHERE status = 'ACTIVE' ORDER BY created_at")
        ).all()
        return [(str(r.id), str(r.organization_id)) for r in rows]


def tick(config: WorkerConfig | None = None) -> PassResult:
    """One complete pass over every active tenant. Safe to call directly."""
    config = config or WorkerConfig()
    result = PassResult()
    factory = get_session_factory()

    for tenant_id, organization_id in _active_tenants():
        result.tenants += 1
        ctx = system_context(tenant_id, organization_id, config.worker_id)
        session = factory()
        try:
            bind_tenant(session, tenant_id, actor=config.worker_id)

            claimed = workflow_engine.claim_due_runs(
                session, config.worker_id, tenant_id=tenant_id, limit=config.run_batch
            )
            for workflow_run_id in claimed:
                workflow_engine.advance(session, ctx, workflow_run_id, worker_id=config.worker_id)
                result.runs_advanced += 1

            delivery = event_bus.dispatch_pending(
                session, tenant_id=tenant_id, batch_size=config.outbox_batch
            )
            result.events_dispatched += int(delivery.get("dispatched", 0))
            result.events_dead += int(delivery.get("dead", 0))

            result.approvals_expired += approval_engine.expire_due_approvals(session, tenant_id)

            telemetry.record_metric(
                session,
                ctx,
                "worker.pass",
                1,
                labels={
                    "worker": config.worker_id,
                    "runs_advanced": len(claimed),
                    "events_dispatched": delivery.get("dispatched", 0),
                },
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001 - one tenant must not stop the rest
            session.rollback()
            message = f"{tenant_id}: {type(exc).__name__}: {exc}"
            result.errors.append(message)
            log.exception("worker pass failed for tenant %s", tenant_id)
        finally:
            session.close()

    return result


class Worker:
    """Long-running loop with graceful shutdown."""

    def __init__(self, config: WorkerConfig | None = None) -> None:
        self.config = config or WorkerConfig()
        self._stop = False

    def request_stop(self, *_: Any) -> None:
        """Finish the pass in flight, then exit. No work is abandoned."""
        log.info("shutdown requested; finishing the current pass")
        self._stop = True

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._handle)

    def _handle(self, _signum: int, _frame: FrameType | None) -> None:
        self.request_stop()

    def run(self) -> int:
        log.info("worker %s starting", self.config.worker_id)
        passes = 0
        while not self._stop:
            result = tick(self.config)
            passes += 1
            if result.did_work or result.errors:
                log.info("pass %s: %s", passes, result.to_dict())
            if self.config.max_passes is not None and passes >= self.config.max_passes:
                break
            if self._stop:
                break
            time.sleep(self.config.poll_seconds if result.did_work else self.config.idle_seconds)
        log.info("worker %s stopped after %s passes", self.config.worker_id, passes)
        return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="agentic-worker", description=__doc__)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--idle-seconds", type=float, default=5.0)
    parser.add_argument("--run-batch", type=int, default=10)
    parser.add_argument("--outbox-batch", type=int, default=50)
    parser.add_argument(
        "--once", action="store_true", help="run a single pass and exit (used by CI and cron)"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = WorkerConfig(
        poll_seconds=args.poll_seconds,
        idle_seconds=args.idle_seconds,
        run_batch=args.run_batch,
        outbox_batch=args.outbox_batch,
        max_passes=1 if args.once else None,
    )
    if args.once:
        result = tick(config)
        log.info("single pass: %s", result.to_dict())
        return 1 if result.errors else 0

    worker = Worker(config)
    worker.install_signal_handlers()
    return worker.run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
