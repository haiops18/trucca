"""Dependency dùng chung: lấy user hiện tại, kiểm tra quyền, CSRF."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import Depends, HTTPException, Request

from ..auth.service import CurrentUser, session_user

SESSION_COOKIE = "trucca_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class PageRedirect(Exception):
    """Ném ra trong route trang HTML để chuyển hướng (vd chưa đăng nhập)."""

    def __init__(self, path: str):
        self.path = path


def state(request: Request) -> Any:
    return request.app.state.trucca


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def optional_user(request: Request) -> CurrentUser | None:
    if hasattr(request.state, "user"):
        return request.state.user
    st = state(request)
    user = session_user(st.engine, request.cookies.get(SESSION_COOKIE), st.config.auth.session_ttl_hours)
    request.state.user = user
    return user


def _check_csrf(request: Request, user: CurrentUser) -> None:
    if request.method in SAFE_METHODS:
        return
    sent = request.headers.get("x-csrf-token", "")
    if not sent or not hmac.compare_digest(sent.encode("utf-8", "replace"), user.csrf_token.encode()):
        raise HTTPException(403, detail={"code": "csrf", "message": "Phiên làm việc không hợp lệ, hãy tải lại trang"})


def api_user(request: Request, user: CurrentUser | None = Depends(optional_user)) -> CurrentUser:
    if user is None:
        raise HTTPException(401, detail={"code": "unauthenticated", "message": "Bạn cần đăng nhập"})
    _check_csrf(request, user)
    return user


def api_user_ready(user: CurrentUser = Depends(api_user)) -> CurrentUser:
    """User đã đăng nhập VÀ đã đổi mật khẩu lần đầu."""
    if user.must_change_password:
        raise HTTPException(403, detail={"code": "must_change_password", "message": "Bạn cần đổi mật khẩu trước"})
    return user


def api_admin(user: CurrentUser = Depends(api_user_ready)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(403, detail={"code": "forbidden", "message": "Chức năng chỉ dành cho quản trị viên"})
    return user


def page_user(request: Request, user: CurrentUser | None = Depends(optional_user)) -> CurrentUser:
    if user is None:
        raise PageRedirect("/login")
    if user.must_change_password and request.url.path.rstrip("/") != f"{state(request).base}/account":
        raise PageRedirect("/account")
    return user


def page_admin(user: CurrentUser = Depends(page_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(403, detail={"code": "forbidden", "message": "Chức năng chỉ dành cho quản trị viên"})
    return user
