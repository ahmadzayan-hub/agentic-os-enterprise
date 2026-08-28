"""Measure the governed path under concurrency.

Writes ``artifacts/performance.json``, which
``tests/performance/test_slo_conformance.py`` then asserts against the SLOs
declared in the agent contracts. The point is to replace a declared latency
target with a measured one.

**What this does not measure.** One process, one database, one host, a seeded
corpus. That is a concurrency and regression measurement, not a capacity
statement about production hardware. The report records the environment so a
number cannot be quoted out of context.

    python scripts/loadtest.py --base http://127.0.0.1:8000 --concurrency 32
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "artifacts" / "performance.json"


@dataclass
class Sample:
    latency_ms: float
    status: int
    error: str = ""


@dataclass
class Scenario:
    name: str
    method: str
    path: str
    describes: str
    authenticated: bool = True
    json_body: dict[str, Any] | None = None
    samples: list[Sample] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        oks = [s.latency_ms for s in self.samples if 200 <= s.status < 300]
        statuses: dict[str, int] = {}
        for s in self.samples:
            statuses[str(s.status)] = statuses.get(str(s.status), 0) + 1
        errors = sorted({s.error for s in self.samples if s.error})
        body: dict[str, Any] = {
            "name": self.name,
            "describes": self.describes,
            "method": self.method,
            "path": self.path,
            "requests": len(self.samples),
            "succeeded": len(oks),
            "success_rate": round(len(oks) / len(self.samples), 4) if self.samples else 0.0,
            "status_counts": statuses,
            "errors": errors[:5],
        }
        if oks:
            ordered = sorted(oks)
            body |= {
                "p50_ms": round(statistics.median(ordered), 2),
                "p95_ms": round(ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)], 2),
                "p99_ms": round(ordered[min(int(len(ordered) * 0.99), len(ordered) - 1)], 2),
                "max_ms": round(ordered[-1], 2),
                "mean_ms": round(statistics.fmean(ordered), 2),
            }
        return body


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    """Authenticate and return a bearer token.

    The API issues a bearer token; the httpOnly session cookie is set by the web
    tier, which exchanges it server-side. A load test drives the API directly, so
    it carries the token.
    """
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("login returned no access token")
    return token


async def _run_scenario(
    base: str, scenario: Scenario, *, token: str, requests: int, concurrency: int
) -> None:
    limiter = asyncio.Semaphore(concurrency)
    headers = {"authorization": f"Bearer {token}"} if scenario.authenticated else {}

    async with httpx.AsyncClient(base_url=base, timeout=30.0, headers=headers) as client:

        async def one() -> None:
            async with limiter:
                started = time.perf_counter()
                try:
                    response = await client.request(scenario.method, scenario.path, json=scenario.json_body)
                    scenario.samples.append(
                        Sample((time.perf_counter() - started) * 1000, response.status_code)
                    )
                except Exception as exc:  # noqa: BLE001 - a failed request is a datum
                    scenario.samples.append(
                        Sample(
                            (time.perf_counter() - started) * 1000,
                            0,
                            f"{type(exc).__name__}: {exc}"[:200],
                        )
                    )

        await asyncio.gather(*(one() for _ in range(requests)))


async def main_async(args: argparse.Namespace) -> int:
    scenarios = [
        Scenario(
            "health",
            "GET",
            "/health",
            "HTTP and database round trip with no authorization",
            authenticated=False,
        ),
        Scenario("runs_list", "GET", "/api/v1/runs", "authorization plus an RLS-filtered read"),
        Scenario(
            "knowledge_search",
            "POST",
            "/api/v1/knowledge/search",
            "hybrid retrieval with the ACL predicate inside the SQL",
            json_body={"query": "rolling stock maintenance interval", "limit": 5},
        ),
        Scenario(
            "command_center",
            "GET",
            "/api/v1/command-center",
            "the widest read on the platform, many aggregates in one request",
        ),
        Scenario(
            "decision_queue",
            "GET",
            "/api/v1/decisions",
            "the decision queue with the domain-membership predicate joined in",
        ),
        Scenario(
            "decision_effectiveness",
            "GET",
            "/api/v1/decisions/effectiveness",
            "the North Star aggregate, a lateral join per decision",
        ),
    ]

    async with httpx.AsyncClient(base_url=args.base, timeout=30.0) as client:
        token = await _login(client, args.email, args.password)

    levels = [int(c) for c in str(args.concurrency).split(",")]
    started_at = time.time()
    passes: list[dict[str, Any]] = []
    total = 0

    # A single concurrency level is a number; a sweep is a characterisation.
    # Comparing level 1 with the highest level separates per-request cost from
    # queueing, which is what tells you whether the platform is saturated.
    for level in levels:
        pass_started = time.time()
        fresh = [
            Scenario(s.name, s.method, s.path, s.describes, s.authenticated, s.json_body) for s in scenarios
        ]
        for scenario in fresh:
            print(f"  concurrency {level:>3}  {scenario.name}: {args.requests} requests")
            await _run_scenario(
                args.base,
                scenario,
                token=token,
                requests=args.requests,
                concurrency=level,
            )
        pass_elapsed = time.time() - pass_started
        pass_total = sum(len(s.samples) for s in fresh)
        total += pass_total
        passes.append(
            {
                "concurrency": level,
                "wall_clock_seconds": round(pass_elapsed, 2),
                "throughput_rps": round(pass_total / pass_elapsed, 1) if pass_elapsed else 0.0,
                "scenarios": [s.summary() for s in fresh],
            }
        )
    elapsed = time.time() - started_at
    report = {
        "tool": "scripts/loadtest.py",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "note": (
                "One API process, one PostgreSQL instance, one host, a seeded corpus. "
                "A concurrency and regression measurement, not a production capacity "
                "statement."
            ),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": __import__("os").cpu_count(),
        },
        "parameters": {
            "requests_per_scenario": args.requests,
            "concurrency_levels": levels,
        },
        "wall_clock_seconds": round(elapsed, 2),
        "total_requests": total,
        "passes": passes,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"\nwrote {out}")
    for entry in passes:
        print(f"\nconcurrency {entry['concurrency']}  ({entry['throughput_rps']} req/s)")
        for scenario in entry["scenarios"]:
            if "p95_ms" in scenario:
                print(
                    f"  {scenario['name']:<18} p50 {scenario['p50_ms']:>8.2f}ms  "
                    f"p95 {scenario['p95_ms']:>8.2f}ms  p99 {scenario['p99_ms']:>8.2f}ms  "
                    f"success {scenario['success_rate']:.1%}"
                )
            else:
                print(f"  {scenario['name']:<18} no successful requests: {scenario['status_counts']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="systems.lead@rta.example")
    parser.add_argument("--password", default="AgenticOS-Demo-2026!")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument(
        "--concurrency",
        default="1,8,32",
        help="comma-separated levels, e.g. 1,8,32 — level 1 is the baseline",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
