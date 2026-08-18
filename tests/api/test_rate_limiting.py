"""Rate limiting, in-process and shared.

The shared limiter is tested against a real Redis. A stub would only prove the
stub matches my expectations; the point of the Lua script is that Redis
evaluates it atomically, and only Redis can demonstrate that.
"""

from __future__ import annotations

import os
import secrets

import pytest
from agentic_os.api.ratelimit import (
    InProcessRateLimiter,
    SharedRateLimiter,
    build_rate_limiter,
    hash_key,
)

pytestmark = [pytest.mark.unit]

REDIS_URL = os.environ.get("AGENTIC_REDIS_URL", "redis://127.0.0.1:6379/15")


def _redis():
    try:
        import redis

        client = redis.Redis.from_url(REDIS_URL)
        client.ping()
        return client
    except Exception:
        return None


requires_redis = pytest.mark.skipif(_redis() is None, reason="no Redis at AGENTIC_REDIS_URL")


# ------------------------------------------------------------------ in-process
def test_in_process_limiter_admits_up_to_the_limit_then_refuses() -> None:
    limiter = InProcessRateLimiter(3)
    assert [limiter.allow("a")[0] for _ in range(4)] == [True, True, True, False]


def test_in_process_limiter_counts_each_principal_separately() -> None:
    limiter = InProcessRateLimiter(1)
    assert limiter.allow("a")[0] is True
    assert limiter.allow("b")[0] is True
    assert limiter.allow("a")[0] is False


# ---------------------------------------------------------------------- shared
@requires_redis
def test_shared_limiter_admits_up_to_the_limit_then_refuses() -> None:
    client = _redis()
    client.flushdb()
    limiter = SharedRateLimiter(3, client, namespace="t1")
    assert [limiter.allow("caller")[0] for _ in range(4)] == [True, True, True, False]


@requires_redis
def test_the_limit_is_the_deployments_not_one_replicas() -> None:
    """The defect this replaces: two replicas each allowed a full quota."""
    client = _redis()
    client.flushdb()
    replica_a = SharedRateLimiter(4, client, namespace="t2")
    replica_b = SharedRateLimiter(4, client, namespace="t2")

    verdicts = [
        replica_a.allow("caller")[0],
        replica_b.allow("caller")[0],
        replica_a.allow("caller")[0],
        replica_b.allow("caller")[0],
        replica_a.allow("caller")[0],  # fifth request overall
        replica_b.allow("caller")[0],
    ]
    assert verdicts == [True, True, True, True, False, False], (
        "the fifth request must be refused whichever replica receives it"
    )


@requires_redis
def test_shared_limiter_counts_each_principal_separately() -> None:
    client = _redis()
    client.flushdb()
    limiter = SharedRateLimiter(1, client, namespace="t3")
    assert limiter.allow("a")[0] is True
    assert limiter.allow("b")[0] is True
    assert limiter.allow("a")[0] is False


@requires_redis
def test_requests_in_the_same_millisecond_are_all_counted() -> None:
    """A sorted set keyed only by timestamp would collapse them into one."""
    client = _redis()
    client.flushdb()
    limiter = SharedRateLimiter(50, client, namespace="t4")
    for _ in range(20):
        limiter.allow("burst")
    stored = client.zcard(f"t4:{hash_key('burst')}")
    assert stored == 20, f"expected 20 counted requests, Redis holds {stored}"


@requires_redis
def test_no_token_material_is_written_to_redis() -> None:
    """The key is derived from an Authorization header; Redis must not hold it."""
    client = _redis()
    client.flushdb()
    # Generated, not written down. A committed literal that looks like a token
    # is a secret-scan finding in its own right — which is exactly what
    # happened when this test first carried a hardcoded one.
    secret_suffix = secrets.token_urlsafe(24)
    SharedRateLimiter(5, client, namespace="t5").allow(secret_suffix)

    keys = [k.decode() for k in client.keys("t5:*")]
    assert keys, "the limiter recorded nothing"
    for key in keys:
        assert secret_suffix not in key
    assert keys == [f"t5:{hash_key(secret_suffix)}"]


@requires_redis
def test_remaining_counts_down() -> None:
    client = _redis()
    client.flushdb()
    limiter = SharedRateLimiter(3, client, namespace="t6")
    assert [limiter.allow("c")[1] for _ in range(3)] == [2, 1, 0]


# ------------------------------------------------------------------ degradation
def test_an_unreachable_shared_counter_degrades_rather_than_failing() -> None:
    """A cache outage must not become an API outage, nor remove the control."""

    class Broken:
        def register_script(self, _script):
            def _run(**_kwargs):
                raise ConnectionError("redis is down")

            return _run

    limiter = SharedRateLimiter(2, Broken())
    # Still bounded — by the in-process fallback rather than globally.
    assert [limiter.allow("x")[0] for _ in range(3)] == [True, True, False]


def test_the_factory_falls_back_when_no_redis_is_configured() -> None:
    assert isinstance(build_rate_limiter(10, ""), InProcessRateLimiter)


def test_an_unreachable_url_still_yields_a_working_limiter() -> None:
    """redis-py connects lazily, so the failure surfaces on the first request.

    What matters is not which class comes back but that the caller is still
    bounded and never sees an exception.
    """
    limiter = build_rate_limiter(2, "redis://nonexistent.invalid:6379/0")
    assert [limiter.allow("x")[0] for _ in range(3)] == [True, True, False]
