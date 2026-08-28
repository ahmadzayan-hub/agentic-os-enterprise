"""Identifier and clock helpers.

Time is injected through :func:`utcnow` so tests can freeze it, and identifiers
are ULID-like (lexicographically sortable) so that audit chains and run steps
order naturally.
"""

from __future__ import annotations

import os
import secrets
import time
import uuid
from datetime import UTC, datetime

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def _encode_base32(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid() -> str:
    """Return a 26-character lexicographically sortable identifier."""
    timestamp_ms = int(time.time() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode_base32(timestamp_ms, 10) + _encode_base32(randomness, 16)


def prefixed_id(prefix: str) -> str:
    """Return a namespaced sortable identifier, e.g. ``run_01HX...``."""
    return f"{prefix}_{new_ulid()}"


def correlation_id() -> str:
    return prefixed_id("cor")


def idempotency_key() -> str:
    return prefixed_id("idem")


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
