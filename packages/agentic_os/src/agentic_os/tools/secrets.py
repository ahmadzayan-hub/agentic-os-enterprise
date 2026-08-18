"""Secret broker.

Credentials are resolved at the moment of execution, injected into the outbound
request by the gateway, and discarded. They are never returned to a caller,
never placed in a model context, and never written to the audit ledger — only a
fingerprint is recorded, which is enough to prove which credential was used
without disclosing it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.config import get_settings
from agentic_os.core.crypto import LocalKms, sha256_hex
from agentic_os.core.errors import NotFound, NotImplementedCapability


@dataclass(frozen=True, slots=True)
class SecretHandle:
    """A reference to a secret. Deliberately does not carry the value."""

    key: str
    fingerprint: str
    scopes: frozenset[str]
    backend: str

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"SecretHandle(key={self.key!r}, fingerprint={self.fingerprint[:12]}...)"


class SecretBroker:
    """Resolves credentials for the tool gateway only."""

    def __init__(self, session: Session | None = None) -> None:
        self._session = session
        self._settings = get_settings()

    # -- backends ----------------------------------------------------------
    def _from_env(self, key: str) -> str | None:
        return os.environ.get(f"AGENTIC_SECRET_{key.upper().replace('.', '_').replace('-', '_')}")

    def _from_file(self, key: str) -> str | None:
        path = Path(self._settings.secret_file_path)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        value = payload.get(key)
        return str(value) if value is not None else None

    def _from_database(self, connector_key: str, credential_key: str, tenant_id: str) -> str | None:
        if self._session is None:
            return None
        row = self._session.execute(
            text(
                """
                SELECT cc.ciphertext, cc.kms_backend, cc.expires_at
                FROM connector_credentials cc
                JOIN connectors c ON c.id = cc.connector_id
                WHERE cc.tenant_id = :t AND c.connector_key = :ck
                  AND cc.credential_key = :credk
                """
            ),
            {"t": tenant_id, "ck": connector_key, "credk": credential_key},
        ).mappings().first()
        if row is None:
            return None
        from agentic_os.core.ids import utcnow

        if row["expires_at"] is not None and row["expires_at"] <= utcnow():
            raise NotFound(
                f"credential '{credential_key}' for connector '{connector_key}' has expired"
            )
        kms = LocalKms.from_config(self._settings.kms_local_key)
        return kms.decrypt(row["ciphertext"], aad=f"{connector_key}:{credential_key}").decode("utf-8")

    # -- public surface ----------------------------------------------------
    def resolve(
        self, key: str, *, tenant_id: str = "", connector_key: str = ""
    ) -> tuple[str, SecretHandle]:
        """Return (value, handle). Only the gateway calls this."""
        backend = self._settings.secret_backend
        value: str | None = None

        if connector_key and tenant_id:
            value = self._from_database(connector_key, key, tenant_id)
            if value is not None:
                backend = "database"
        if value is None:
            if backend == "env":
                value = self._from_env(key)
            elif backend == "file":
                value = self._from_file(key)
            elif backend == "vault":  # pragma: no cover - external dependency
                raise NotImplementedCapability(
                    "the Vault secret backend is declared but no Vault client is wired; "
                    "use 'file' or 'database' until it is"
                )

        if value is None:
            raise NotFound(f"secret '{key}' is not configured", details={"backend": backend})

        return value, SecretHandle(
            key=key,
            fingerprint=sha256_hex(value),
            scopes=frozenset(),
            backend=backend,
        )

    def store(
        self,
        tenant_id: str,
        connector_key: str,
        credential_key: str,
        value: str,
        *,
        scopes: list[str] | None = None,
        rotation_days: int = 90,
    ) -> SecretHandle:
        """Store a credential as a KMS envelope bound to connector and key."""
        if self._session is None:
            raise NotImplementedCapability("storing credentials requires a database session")
        from datetime import timedelta

        from agentic_os.core.ids import utcnow

        connector = self._session.execute(
            text("SELECT id FROM connectors WHERE tenant_id = :t AND connector_key = :k"),
            {"t": tenant_id, "k": connector_key},
        ).first()
        if connector is None:
            raise NotFound(f"connector '{connector_key}' is not registered")

        kms = LocalKms.from_config(self._settings.kms_local_key)
        ciphertext = kms.encrypt(value.encode("utf-8"), aad=f"{connector_key}:{credential_key}")
        fingerprint = sha256_hex(value)
        self._session.execute(
            text(
                """
                INSERT INTO connector_credentials (tenant_id, connector_id, credential_key,
                                                   ciphertext, kms_backend, fingerprint,
                                                   scopes, rotation_due_at)
                VALUES (:t, :c, :k, :ct, :b, :fp, :scopes, :due)
                ON CONFLICT (connector_id, credential_key) DO UPDATE
                  SET ciphertext = EXCLUDED.ciphertext, fingerprint = EXCLUDED.fingerprint,
                      scopes = EXCLUDED.scopes, rotated_at = now(),
                      rotation_due_at = EXCLUDED.rotation_due_at
                """
            ),
            {
                "t": tenant_id,
                "c": connector.id,
                "k": credential_key,
                "ct": ciphertext,
                "b": self._settings.kms_backend,
                "fp": fingerprint,
                "scopes": scopes or [],
                "due": utcnow() + timedelta(days=rotation_days),
            },
        )
        return SecretHandle(
            key=credential_key,
            fingerprint=fingerprint,
            scopes=frozenset(scopes or []),
            backend="database",
        )

    def rotation_due(self, tenant_id: str) -> list[dict[str, Any]]:
        if self._session is None:
            return []
        rows = self._session.execute(
            text(
                """
                SELECT c.connector_key, cc.credential_key, cc.rotation_due_at, cc.rotated_at
                FROM connector_credentials cc
                JOIN connectors c ON c.id = cc.connector_id
                WHERE cc.tenant_id = :t
                  AND cc.rotation_due_at IS NOT NULL AND cc.rotation_due_at <= now()
                ORDER BY cc.rotation_due_at
                """
            ),
            {"t": tenant_id},
        ).mappings()
        return [dict(r) for r in rows]
