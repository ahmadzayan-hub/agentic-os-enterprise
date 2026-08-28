"""Cryptographic helpers: password hashing, hash chaining and local KMS.

The KMS abstraction exists so that production deployments can swap the local
data key for AWS KMS / Azure Key Vault / GCP KMS without touching call sites.
The local backend is explicitly rejected by configuration validation in
staging and production.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any, Protocol

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        return True


def canonical_json(payload: Any) -> str:
    """Stable JSON encoding used for every hash that must be reproducible."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_hash(payload: Any) -> str:
    return sha256_hex(canonical_json(payload))


def chain_hash(previous_hash: str, payload: Any) -> str:
    """Hash-chain link: H(prev || canonical(payload))."""
    return sha256_hex(f"{previous_hash}|{canonical_json(payload)}")


def hmac_sign(key: bytes, payload: Any) -> str:
    return hmac.new(key, canonical_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()


def hmac_verify(key: bytes, payload: Any, signature: str) -> bool:
    return hmac.compare_digest(hmac_sign(key, payload), signature)


class KeyManagementService(Protocol):
    """Minimal envelope-encryption surface used by the secret broker."""

    def encrypt(self, plaintext: bytes, *, aad: str = "") -> str: ...

    def decrypt(self, ciphertext: str, *, aad: str = "") -> bytes: ...

    @property
    def backend(self) -> str: ...


class LocalKms:
    """Development KMS using an HMAC-derived keystream.

    This is deliberately simple and is blocked in staging/production by
    :meth:`Settings.validate_for_boot`. It provides confidentiality against
    casual disclosure in local environments and correct AAD binding semantics
    so that call sites are exercised the same way as in production.
    """

    backend = "local"

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or os.urandom(32)

    @classmethod
    def from_config(cls, encoded_key: str) -> LocalKms:
        if encoded_key:
            return cls(base64.b64decode(encoded_key))
        return cls()

    def _keystream(self, nonce: bytes, length: int, aad: str) -> bytes:
        out = bytearray()
        counter = 0
        while len(out) < length:
            block = hmac.new(
                self._key,
                nonce + aad.encode("utf-8") + counter.to_bytes(4, "big"),
                hashlib.sha256,
            ).digest()
            out.extend(block)
            counter += 1
        return bytes(out[:length])

    def encrypt(self, plaintext: bytes, *, aad: str = "") -> str:
        nonce = os.urandom(16)
        stream = self._keystream(nonce, len(plaintext), aad)
        ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream, strict=True))
        tag = hmac.new(self._key, nonce + ciphertext + aad.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(nonce + tag + ciphertext).decode("ascii")

    def decrypt(self, ciphertext: str, *, aad: str = "") -> bytes:
        raw = base64.b64decode(ciphertext)
        nonce, tag, body = raw[:16], raw[16:48], raw[48:]
        expected = hmac.new(self._key, nonce + body + aad.encode("utf-8"), hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("KMS ciphertext failed integrity check")
        stream = self._keystream(nonce, len(body), aad)
        return bytes(a ^ b for a, b in zip(body, stream, strict=True))


def redact(value: str, keep: int = 4) -> str:
    """Redact a secret for logging, retaining a short suffix for correlation."""
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
