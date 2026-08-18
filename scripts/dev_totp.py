"""Print a current TOTP code for a seeded development user.

Used by the screenshot capture, which has to sign in as a privileged role and
therefore has to present a second factor. It reads the enrolment through the
platform's own KMS envelope rather than storing a plaintext secret anywhere,
and it refuses to run outside a development or test environment.
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from agentic_os.core.config import get_settings
from agentic_os.core.db import provisioning_session_scope
from agentic_os.identity.mfa import _kms, totp_now


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: dev_totp.py <email>", file=sys.stderr)
        return 2
    email = argv[0].strip().lower()

    settings = get_settings()
    if settings.app_env not in ("development", "test"):
        print(f"refusing to mint a factor in '{settings.app_env}'", file=sys.stderr)
        return 3

    with provisioning_session_scope() as session:
        row = (
            session.execute(
                text(
                    "SELECT u.id, m.secret_ciphertext, m.digits, m.period_seconds "
                    "FROM users u JOIN user_mfa m ON m.user_id = u.id WHERE u.email = :e"
                ),
                {"e": email},
            )
            .mappings()
            .first()
        )
        if row is None:
            print(f"no MFA enrolment for {email}", file=sys.stderr)
            return 4
        secret = _kms().decrypt(row["secret_ciphertext"], aad=f"user:{row['id']}").decode("utf-8")
        print(totp_now(secret, digits=int(row["digits"]), period=int(row["period_seconds"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
