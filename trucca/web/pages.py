"""Các trang HTML (server render khung trang, dữ liệu nạp bằng JS)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import __version__
from ..auth.service import CurrentUser
from .deps import PASSWORD_PAGE, optional_user, page_admin, page_user, state

router = APIRouter()


def render(request: Request, template: str, user: CurrentUser | None, **ctx: Any) -> HTMLResponse:
    st = state(request)
    html = st.templates.get_template(template).render(
        base=st.base, user=user, version=__version__, app_name="TrucCa", path=request.url.path, **ctx
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: CurrentUser | None = Depends(optional_user)) -> Response:
    if user is not None:
        return RedirectResponse(f"{state(request).base}/", status_code=303)
    return render(request, "login.html", None)


@router.get("/", response_class=HTMLResponse)
def alerts_page(request: Request, user: CurrentUser = Depends(page_user)) -> Response:
    return render(request, "alerts.html", user, active="alerts")


@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, user: CurrentUser = Depends(page_user)) -> Response:
    return render(request, "reports.html", user, active="reports")


# --------------------------------------------------------------------------- module Quản trị
# Admin: Người dùng, Hệ thống, Đổi mật khẩu. Người dùng thường: chỉ Đổi mật khẩu (của chính mình).
# Quyền được kiểm tra ở từng route/API (page_admin, api_admin), không chỉ ẩn trên giao diện.

ADMIN_SECTIONS = [
    {"key": "users", "label": "Người dùng", "path": "/admin/users", "admin_only": True},
    {"key": "system", "label": "Hệ thống", "path": "/admin/system", "admin_only": True},
    {"key": "password", "label": "Đổi mật khẩu", "path": PASSWORD_PAGE, "admin_only": False},
]


def admin_sections(user: CurrentUser) -> list[dict[str, Any]]:
    if user.must_change_password:
        return [s for s in ADMIN_SECTIONS if s["key"] == "password"]
    return [s for s in ADMIN_SECTIONS if user.is_admin or not s["admin_only"]]


def render_admin(request: Request, template: str, user: CurrentUser, section: str, **ctx: Any) -> HTMLResponse:
    current = next(s for s in ADMIN_SECTIONS if s["key"] == section)
    return render(request, template, user, active="admin", section=section, section_label=current["label"],
                  sections=admin_sections(user), **ctx)


@router.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, user: CurrentUser = Depends(page_user)) -> Response:
    """Vào mục đầu tiên người dùng được phép xem."""
    first = admin_sections(user)[0]
    return RedirectResponse(f"{state(request).base}{first['path']}", status_code=302)


@router.get("/admin/password", response_class=HTMLResponse)
def password_page(request: Request, user: CurrentUser = Depends(page_user)) -> Response:
    return render_admin(request, "admin_password.html", user, "password",
                        min_password_length=state(request).config.auth.min_password_length)


@router.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, user: CurrentUser = Depends(page_admin)) -> Response:
    return render_admin(request, "admin_users.html", user, "users",
                        min_password_length=state(request).config.auth.min_password_length)


@router.get("/admin/system", response_class=HTMLResponse)
def system_page(request: Request, user: CurrentUser = Depends(page_admin)) -> Response:
    return render_admin(request, "admin_system.html", user, "system")


# URL cũ (trước khi gộp module Quản trị) -> URL mới, giữ bookmark. 301 vì đây là chuyển vĩnh viễn.
LEGACY_REDIRECTS = {"/account": PASSWORD_PAGE}


def _legacy(target: str):  # type: ignore[no-untyped-def]
    def handler(request: Request) -> Response:
        return RedirectResponse(f"{state(request).base}{target}", status_code=301)
    return handler


for _old, _new in LEGACY_REDIRECTS.items():
    router.add_api_route(_old, _legacy(_new), methods=["GET"], include_in_schema=False)
