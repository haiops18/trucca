"""Các trang HTML (server render khung trang, dữ liệu nạp bằng JS)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import __version__
from ..auth.service import CurrentUser
from .deps import optional_user, page_admin, page_user, state

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


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, user: CurrentUser = Depends(page_user)) -> Response:
    return render(request, "account.html", user, active="account",
                  min_password_length=state(request).config.auth.min_password_length)


@router.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, user: CurrentUser = Depends(page_admin)) -> Response:
    return render(request, "admin_users.html", user, active="users",
                  min_password_length=state(request).config.auth.min_password_length)


@router.get("/admin/system", response_class=HTMLResponse)
def system_page(request: Request, user: CurrentUser = Depends(page_admin)) -> Response:
    return render(request, "admin_system.html", user, active="system")
