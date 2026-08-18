"""Authentication, MFA and session behaviour against the live database."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from agentic_os.core.config import get_settings
from agentic_os.core.crypto import LocalKms
from agentic_os.core.db import bind_tenant, get_session_factory
from agentic_os.core.errors import AuthenticationError
from agentic_os.identity.authn import (
    authenticate_password,
    issue_access_token,
    revoke_session,
    session_is_active,
    verify_access_token,
)
from agentic_os.identity.mfa import totp_now
from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, pytest.mark.security, requires_db]

NON_MFA_USER = "analyst@rta.example"
MFA_USER = "auditor@rta.example"


@pytest.fixture()
def session() -> Session:
    s = get_session_factory()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _totp_for(session: Session, email: str) -> str:
    """Return a currently valid TOTP code, resetting replay state first.

    The replay counter is persistent by design, so two suite runs inside the
    same 30-second window would otherwise collide on an already-consumed code.
    The test owns this user, so it resets the counter to establish a known
    precondition rather than depending on wall-clock luck.
    """
    row = session.execute(
        text("SELECT id, tenant_id FROM auth_bootstrap_user(:e)"), {"e": email}
    ).one()
    bind_tenant(session, str(row.tenant_id))
    session.execute(
        text("UPDATE user_mfa SET last_counter = 0 WHERE user_id = :u"), {"u": row.id}
    )
    session.commit()
    ciphertext = session.execute(
        text("SELECT secret_ciphertext FROM auth_bootstrap_mfa(:u)"), {"u": row.id}
    ).scalar_one()
    secret = (
        LocalKms.from_config(get_settings().kms_local_key)
        .decrypt(ciphertext, aad=f"user:{row.id}")
        .decode("utf-8")
    )
    return totp_now(secret)


def test_valid_password_authenticates(session: Session, demo_password: str) -> None:
    principal = authenticate_password(session, NON_MFA_USER, demo_password)
    session.commit()
    assert principal.email == NON_MFA_USER
    assert "analyst" in principal.roles
    assert principal.permissions
    assert principal.tenant_id


def test_wrong_password_is_rejected(session: Session) -> None:
    with pytest.raises(AuthenticationError):
        authenticate_password(session, NON_MFA_USER, "definitely-not-the-password")


def test_unknown_user_is_rejected_without_disclosing_existence(session: Session) -> None:
    with pytest.raises(AuthenticationError) as excinfo:
        authenticate_password(session, "nobody@nowhere.test", "whatever-password")
    assert excinfo.value.message == "Invalid credentials"


def test_email_is_case_insensitive(session: Session, demo_password: str) -> None:
    principal = authenticate_password(session, NON_MFA_USER.upper(), demo_password)
    session.commit()
    assert principal.email == NON_MFA_USER


def test_mfa_required_account_fails_closed_without_a_code(
    session: Session, demo_password: str
) -> None:
    with pytest.raises(AuthenticationError) as excinfo:
        authenticate_password(session, MFA_USER, demo_password)
    assert excinfo.value.details.get("mfa_required") is True


def test_mfa_required_account_rejects_a_wrong_code(session: Session, demo_password: str) -> None:
    with pytest.raises(AuthenticationError):
        authenticate_password(session, MFA_USER, demo_password, mfa_code="000000")


def test_mfa_code_authenticates_and_cannot_be_replayed(
    session: Session, demo_password: str
) -> None:
    code = _totp_for(session, MFA_USER)
    principal = authenticate_password(session, MFA_USER, demo_password, mfa_code=code)
    session.commit()
    assert principal.mfa_satisfied is True

    with pytest.raises(AuthenticationError):
        authenticate_password(session, MFA_USER, demo_password, mfa_code=code)


def test_access_token_round_trips(session: Session, demo_password: str) -> None:
    principal = authenticate_password(session, NON_MFA_USER, demo_password)
    session.commit()
    tokens = issue_access_token(principal)
    decoded = verify_access_token(tokens["access_token"])
    assert decoded.user_id == principal.user_id
    assert decoded.tenant_id == principal.tenant_id
    assert decoded.permissions == principal.permissions


def test_tampered_token_is_rejected(session: Session, demo_password: str) -> None:
    principal = authenticate_password(session, NON_MFA_USER, demo_password)
    session.commit()
    token = issue_access_token(principal)["access_token"]
    header, payload, signature = token.split(".")
    forged = f"{header}.{payload}.{signature[:-4]}AAAA"
    with pytest.raises(AuthenticationError):
        verify_access_token(forged)


def test_token_signed_with_another_key_is_rejected() -> None:
    import time

    import jwt

    settings = get_settings()
    claims = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": "attacker",
        "tid": "00000000-0000-0000-0000-000000000000",
        "perms": ["*"],
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
    }
    forged = jwt.encode(claims, "a-completely-different-key", algorithm="HS256")
    with pytest.raises(AuthenticationError):
        verify_access_token(forged)


def test_expired_token_is_rejected() -> None:
    import time

    import jwt

    settings = get_settings()
    claims = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": "u",
        "tid": "00000000-0000-0000-0000-000000000000",
        "iat": int(time.time()) - 7200,
        "exp": int(time.time()) - 3600,
    }
    expired = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
    with pytest.raises(AuthenticationError):
        verify_access_token(expired)


def test_session_can_be_revoked(session: Session, demo_password: str) -> None:
    principal = authenticate_password(session, NON_MFA_USER, demo_password)
    session.commit()
    bind_tenant(session, principal.tenant_id)
    assert session_is_active(session, principal.session_id) is True
    assert revoke_session(session, principal.tenant_id, principal.session_id) is True
    session.commit()
    bind_tenant(session, principal.tenant_id)
    assert session_is_active(session, principal.session_id) is False


def test_repeated_failures_lock_the_account(session: Session, demo_password: str) -> None:
    email = "builder@rta.example"
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            authenticate_password(session, email, "wrong-password-here")

    with pytest.raises(AuthenticationError) as excinfo:
        authenticate_password(session, email, demo_password)
    assert "locked" in excinfo.value.message.lower()

    # Reset so the fixture-shared account stays usable for other tests.
    row = session.execute(
        text("SELECT id, tenant_id FROM auth_bootstrap_user(:e)"), {"e": email}
    ).one()
    bind_tenant(session, str(row.tenant_id))
    session.execute(
        text("UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = :i"),
        {"i": row.id},
    )
    session.commit()


def test_login_writes_an_audit_entry(session: Session, demo_password: str) -> None:
    principal = authenticate_password(session, NON_MFA_USER, demo_password)
    session.commit()
    bind_tenant(session, principal.tenant_id)
    count = session.execute(
        text(
            "SELECT count(*) FROM audit_events "
            "WHERE category = 'AUTH' AND action = 'login.password' AND outcome = 'SUCCESS'"
        )
    ).scalar_one()
    assert count >= 1
