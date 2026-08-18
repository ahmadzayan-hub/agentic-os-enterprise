"""Request rate limiting.

Two implementations behind one interface:

* :class:`InProcessRateLimiter` bounds a single replica. It is correct, needs
  nothing, and is wrong the moment there is more than one replica — the
  effective limit multiplies by the replica count and resets on restart.
* :class:`SharedRateLimiter` bounds the whole deployment through Redis, which
  is already a dependency.

**Degradation is deliberate.** If Redis is unreachable the shared limiter falls
back to its in-process twin rather than failing the request or waving it
through. Failing closed would turn a cache outage into a total outage; failing
open would remove the control entirely. Falling back keeps a real bound in
place — per replica instead of global — and says so in the log.

**Keys are hashed.** The caller's identifier is derived from an Authorization
header, and a shared store is a wider blast radius than process memory, so the
key written to Redis is a SHA-256 digest. A token suffix never leaves the
process.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict, deque
from typing import Protocol

log = logging.getLogger("agentic_os.api.ratelimit")

WINDOW_SECONDS = 60

#: Sliding window in one round trip. Redis evaluates this atomically, so the
#: check and the increment cannot interleave between replicas — the race that
#: makes a naive GET/INCR limiter leak requests under concurrency.
_SLIDING_WINDOW = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local used = redis.call('ZCARD', key)
if used >= limit then
  return {0, 0}
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
return {1, limit - used - 1}
"""


def hash_key(raw: str) -> str:
    """Stable, non-reversible identifier for a caller."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class RateLimiter(Protocol):
    def allow(self, key: str) -> tuple[bool, int]:
        """Return (allowed, remaining) and count the request if allowed."""
        ...


class InProcessRateLimiter:
    """Sliding-window limiter over one process's memory."""

    def __init__(self, limit_per_minute: int) -> None:
        self.limit = limit_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        window = self._hits[key]
        while window and now - window[0] > WINDOW_SECONDS:
            window.popleft()
        if len(window) >= self.limit:
            return False, 0
        window.append(now)
        return True, self.limit - len(window)


class SharedRateLimiter:
    """Sliding-window limiter shared by every replica through Redis."""

    def __init__(self, limit_per_minute: int, client: object, *, namespace: str = "rl") -> None:
        self.limit = limit_per_minute
        self._client = client
        self._namespace = namespace
        self._fallback = InProcessRateLimiter(limit_per_minute)
        self._script = None
        self._degraded = False

    def _register(self):  # type: ignore[no-untyped-def]
        if self._script is None:
            self._script = self._client.register_script(_SLIDING_WINDOW)  # type: ignore[attr-defined]
        return self._script

    def allow(self, key: str) -> tuple[bool, int]:
        now_ms = int(time.time() * 1000)
        # The member must be unique per request or two requests in the same
        # millisecond would collapse into one sorted-set entry and the second
        # would not be counted.
        member = f"{now_ms}-{time.monotonic_ns()}"
        try:
            allowed, remaining = self._register()(
                keys=[f"{self._namespace}:{hash_key(key)}"],
                args=[now_ms, WINDOW_SECONDS * 1000, self.limit, member],
            )
            if self._degraded:
                log.info("rate limiter: shared counter reachable again")
                self._degraded = False
            return bool(allowed), int(remaining)
        except Exception as exc:  # noqa: BLE001 - a cache outage must not 500
            if not self._degraded:
                log.warning(
                    "rate limiter: shared counter unavailable (%s); falling back to "
                    "per-replica limiting until it returns",
                    exc,
                )
                self._degraded = True
            return self._fallback.allow(key)


def build_rate_limiter(limit_per_minute: int, redis_url: str = "") -> RateLimiter:
    """Shared limiter when Redis is configured and importable, else in-process."""
    if not redis_url:
        return InProcessRateLimiter(limit_per_minute)
    try:
        import redis  # noqa: PLC0415 - optional at import time

        return SharedRateLimiter(limit_per_minute, redis.Redis.from_url(redis_url))
    except Exception as exc:  # noqa: BLE001 - never block startup on the cache
        log.warning(
            "rate limiter: cannot use Redis at the configured URL (%s); limiting per replica instead",
            exc,
        )
        return InProcessRateLimiter(limit_per_minute)
