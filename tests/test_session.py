from datetime import timedelta

from fastapi import Response

from trucca.auth.service import session_ttl
from trucca.config import AuthConfig
from trucca.web.deps import SESSION_COOKIE, set_session_cookie


def test_session_ttl_depends_on_remember():
    cfg = AuthConfig(session_ttl_hours=12, remember_days=30)
    assert session_ttl(cfg, False) == timedelta(hours=12)
    assert session_ttl(cfg, True) == timedelta(days=30)


def _cookie(remember: bool, secure: bool = False) -> str:
    resp = Response()
    set_session_cookie(resp, "tok", AuthConfig(remember_days=30, cookie_secure=secure), "/", remember)
    [header] = resp.headers.getlist("set-cookie")
    return header


def test_remember_cookie_is_persistent():
    h = _cookie(True)
    assert h.startswith(f"{SESSION_COOKIE}=tok;")
    assert f"Max-Age={30 * 86400}" in h and "HttpOnly" in h and "SameSite=lax" in h


def test_non_remember_cookie_is_browser_session_only():
    h = _cookie(False)
    assert "Max-Age" not in h and "expires" not in h.lower()


def test_cookie_secure_flag():
    assert "Secure" in _cookie(True, secure=True)
    assert "Secure" not in _cookie(True, secure=False)


def test_remember_days_validation():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AuthConfig(remember_days=0)
