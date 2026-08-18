"""Time-based one-time password (RFC 6238) enrolment and verification.

Secrets are held as KMS envelopes bound to the user id, never in clear at rest
and never in a log line. Verification accepts a small clock-skew window and
records the accepted time step, so a code cannot be replayed within its own
validity window.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from dataclasses import dataclass
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.config import get_settings
from agentic_os.core.crypto import LocalKms
from agentic_os.core.ids import random_token

#: Number of time steps either side of "now" that are accepted.
SKEW_STEPS = 1


def _kms() -> LocalKms:
    settings = get_settings()
    if settings.kms_backend != "local":  # pragma: no cover - provider wiring
        raise NotImplementedError(
            f"KMS backend '{settings.kms_backend}' is declared but not wired; "
            "configure the provider adapter before enabling it"
        )
    return LocalKms.from_config(settings.kms_local_key)


def generate_secret(length: int = 20) -> str:
    """Return a new base32 TOTP secret."""
    raw = base64.b32encode(random_token(length).encode("utf-8")[:length])
    return raw.decode("ascii").rstrip("=")


def provisioning_uri(secret: str, account: str, issuer: str = "Agentic OS") -> str:
    """otpauth:// URI for an authenticator app."""
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}"
        f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )


def _hotp(secret_b32: str, counter: int, digits: int = 6) -> str:
    padding = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + padding)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def totp_now(secret_b32: str, *, digits: int = 6, period: int = 30, at: float | None = None) -> str:
    counter = int((at if at is not None else time.time()) // period)
    return _hotp(secret_b32, counter, digits)


@dataclass(slots=True)
class MfaEnrolment:
    secret: str
    uri: str


def enrol_totp(session: Session, *, tenant_id: str, user_id: str, account: str) -> MfaEnrolment:
    """Create or replace a TOTP enrolment and return the secret once."""
    secret = generate_secret()
    ciphertext = _kms().encrypt(secret.encode("utf-8"), aad=f"user:{user_id}")
    session.execute(
        text(
            """
            INSERT INTO user_mfa (user_id, tenant_id, method, secret_ciphertext, kms_backend)
            VALUES (:u, :t, 'TOTP', :c, :b)
            ON CONFLICT (user_id) DO UPDATE
              SET secret_ciphertext = EXCLUDED.secret_ciphertext,
                  kms_backend = EXCLUDED.kms_backend,
                  last_counter = 0, verified_at = NULL
            """
        ),
        {"u": user_id, "t": tenant_id, "c": ciphertext, "b": get_settings().kms_backend},
    )
    session.execute(
        text("UPDATE users SET mfa_enrolled = true, mfa_secret_ref = :ref WHERE id = :u"),
        {"u": user_id, "ref": f"user_mfa:{user_id}"},
    )
    return MfaEnrolment(secret=secret, uri=provisioning_uri(secret, account))


def user_requires_mfa(session: Session, user_id: str) -> bool:
    """True when any held role demands a second factor."""
    settings = get_settings()
    role_required = session.execute(
        text("SELECT auth_user_requires_mfa(:u) AS required"), {"u": user_id}
    ).scalar_one()
    if role_required:
        return True
    if not settings.mfa_required_roles:
        return False
    rows = session.execute(
        text(
            """
            SELECT r.slug FROM user_roles ur JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = :u AND (ur.expires_at IS NULL OR ur.expires_at > now())
            """
        ),
        {"u": user_id},
    ).all()
    return bool({r.slug for r in rows} & set(settings.mfa_required_roles))


def verify_totp(session: Session, user_id: str, code: str | None) -> bool:
    """Verify a TOTP code, rejecting replays and unenrolled users."""
    if not code or not code.strip().isdigit():
        return False
    row = session.execute(
        text(
            "SELECT method, secret_ciphertext, kms_backend, digits, period_seconds, "
            "last_counter FROM auth_bootstrap_mfa(:u)"
        ),
        {"u": user_id},
    ).mappings().first()
    if row is None or not row["secret_ciphertext"]:
        # Enrolment is required before a factor can be presented. Failing here
        # is the fail-closed path for an account that must use MFA.
        return False
    if row["method"] != "TOTP":  # pragma: no cover - other methods not wired
        raise NotImplementedError(f"MFA method '{row['method']}' is not implemented")

    try:
        secret = _kms().decrypt(row["secret_ciphertext"], aad=f"user:{user_id}").decode("utf-8")
    except ValueError:
        # An envelope that will not open means the enrolment is unusable — a
        # rotated data key, a restored-from-elsewhere row, or tampering. Fail
        # closed rather than surfacing a decryption error at the login boundary.
        return False
    period = int(row["period_seconds"])
    digits = int(row["digits"])
    now_counter = int(time.time() // period)
    candidate = code.strip()

    for offset in range(-SKEW_STEPS, SKEW_STEPS + 1):
        counter = now_counter + offset
        if counter <= int(row["last_counter"]):
            continue  # already consumed
        if hmac.compare_digest(_hotp(secret, counter, digits), candidate):
            accepted = session.execute(
                text("SELECT auth_record_mfa_counter(:u, :c) AS ok"),
                {"u": user_id, "c": counter},
            ).scalar_one()
            return bool(accepted)
    return False
