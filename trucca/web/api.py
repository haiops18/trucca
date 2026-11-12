"""Endpoint JSON nội bộ cho giao diện (yêu cầu đăng nhập; không dành cho hệ thống ngoài)."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..alerts_query import (
    AlertFilter,
    age_buckets,
    fetch_active,
    filter_age,
    filter_options,
    group_rows,
    present_rows,
)
from ..auth import service as auth
from ..auth.service import AuthError, CurrentUser
from ..config import ConfigError
from ..export_xlsx import build_alerts_xlsx
from ..reports import service as reports
from ..reports.service import ReportError
from ..timeutil import current_shift, utcnow
from .deps import SESSION_COOKIE, api_admin, api_user, api_user_ready, client_ip, set_session_cookie, state

log = logging.getLogger(__name__)
router = APIRouter()


def _auth_error(e: AuthError) -> HTTPException:
    return HTTPException(e.status, detail={"code": e.code, "message": e.message})


def _user_json(u: CurrentUser) -> dict[str, Any]:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role,
            "must_change_password": u.must_change_password}


# --------------------------------------------------------------------------- auth


class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    remember: bool = False


@router.post("/auth/login")
def login(body: LoginBody, request: Request) -> JSONResponse:
    st = state(request)
    try:
        token, user = auth.login(st.engine, st.config.auth, body.username, body.password, client_ip(request),
                                 request.headers.get("user-agent"), remember=body.remember)
    except AuthError as e:
        raise _auth_error(e) from e
    resp = JSONResponse({"user": _user_json(user),
                         "redirect": f"{st.base}/account" if user.must_change_password else f"{st.base}/"})
    set_session_cookie(resp, token, st.config.auth, st.base or "/", body.remember)
    return resp


@router.post("/auth/logout")
def logout(request: Request, user: CurrentUser = Depends(api_user)) -> JSONResponse:
    st = state(request)
    auth.logout(st.engine, user, client_ip(request))
    resp = JSONResponse({"ok": True, "redirect": f"{st.base}/login"})
    resp.delete_cookie(SESSION_COOKIE, path=st.base or "/")
    return resp


@router.get("/api/me")
def me(user: CurrentUser = Depends(api_user)) -> dict[str, Any]:
    return _user_json(user)


class PasswordBody(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


@router.post("/api/account/password")
def change_password(body: PasswordBody, request: Request, user: CurrentUser = Depends(api_user)) -> dict[str, Any]:
    st = state(request)
    try:
        auth.change_own_password(st.engine, st.config.auth, user, body.current_password, body.new_password,
                                 client_ip(request))
    except AuthError as e:
        raise _auth_error(e) from e
    return {"ok": True, "redirect": f"{st.base}/"}


# --------------------------------------------------------------------------- alerts


def _alert_filter(q: str, application: str, owner: str, alertname: str, severity: str, sort: str,
                  order: str) -> AlertFilter:
    return AlertFilter(q, application, owner, alertname, severity, sort, order)


def _rows(request: Request, f: AlertFilter, group: bool, min_hours: float | None, max_hours: float | None,
          with_buckets: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Trả về (dòng hiển thị, nhóm tuổi). Nhóm tuổi tính trên tập đã lọc, TRƯỚC khi lọc theo tuổi."""
    st = state(request)
    now = utcnow()
    raw = fetch_active(st.engine, f)
    buckets = age_buckets(raw, now, st.config.report.handover_threshold_hours) if with_buckets else []
    raw = filter_age(raw, now, min_hours, max_hours)
    missing = st.config.display.missing_label_text
    rows = group_rows(raw, now, missing) if group else present_rows(raw, now, missing)
    return rows, buckets


@router.get("/api/alerts")
def list_alerts(
    request: Request,
    q: str = Query("", max_length=200),
    application: str = Query("", max_length=500),
    owner: str = Query("", max_length=500),
    alertname: str = Query("", max_length=500),
    severity: str = Query("", max_length=100),
    sort: str = "duration",
    order: str = "desc",
    group: bool = False,
    min_hours: float | None = Query(None, ge=0),
    max_hours: float | None = Query(None, gt=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=1000),
    _: CurrentUser = Depends(api_user_ready),
) -> dict[str, Any]:
    st = state(request)
    rows, buckets = _rows(request, _alert_filter(q, application, owner, alertname, severity, sort, order), group,
                          min_hours, max_hours, with_buckets=True)
    total = len(rows)
    pages = max(1, math.ceil(total / page_size))
    page = min(page, pages)
    items = rows[(page - 1) * page_size: page * page_size]
    return {
        "items": items,
        "total": total,
        "total_alerts": sum(r["count"] for r in rows),
        "page": page,
        "pages": pages,
        "page_size": page_size,
        "age_buckets": buckets,
        "handover_threshold_hours": st.config.report.handover_threshold_hours,
        "generated_at": utcnow().isoformat(),
        "collector": st.collector.status.as_dict(),
    }


@router.get("/api/alerts/filters")
def alert_filters(request: Request, _: CurrentUser = Depends(api_user_ready)) -> dict[str, list[str]]:
    return filter_options(state(request).engine)


@router.get("/api/alerts/export.xlsx")
def export_alerts(
    request: Request,
    q: str = Query("", max_length=200),
    application: str = Query("", max_length=500),
    owner: str = Query("", max_length=500),
    alertname: str = Query("", max_length=500),
    severity: str = Query("", max_length=100),
    sort: str = "duration",
    order: str = "desc",
    group: bool = False,
    min_hours: float | None = Query(None, ge=0),
    max_hours: float | None = Query(None, gt=0),
    user: CurrentUser = Depends(api_user_ready),
) -> Response:
    st = state(request)
    f = _alert_filter(q, application, owner, alertname, severity, sort, order)
    rows, _ = _rows(request, f, group, min_hours, max_hours)
    now = utcnow()
    content = build_alerts_xlsx(rows, now)
    local = now.astimezone(st.config.tz)
    filename = f"canh-bao-dang-dien-ra_{local:%Y%m%d_%H%M}.xlsx"
    log.info("alerts_exported", extra={"user": user.username, "rows": len(rows)})
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/api/status")
def status(request: Request, _: CurrentUser = Depends(api_user_ready)) -> dict[str, Any]:
    st = state(request)
    cfg = st.config
    now = utcnow()
    cur = current_shift(cfg.shifts, now, cfg.tz)
    return {
        "version": __version__,
        "server_time": now.isoformat(),
        "timezone": cfg.timezone,
        "refresh_seconds": cfg.display.refresh_seconds,
        "group_default": cfg.display.group_same_alert,
        "page_size": cfg.display.page_size,
        "collector": st.collector.status.as_dict(),
        "poll_interval_seconds": cfg.alertmanager.poll_interval_seconds,
        "shifts": [{"name": s.name, "label": s.label} for s in cfg.shifts],
        "current_shift": {"name": cur.name, "date": cur.report_date.isoformat()} if cur else None,
    }


# --------------------------------------------------------------------------- reports


def _check_date(d: date) -> None:
    today = utcnow().date()
    if d > today + timedelta(days=1) or d < today - timedelta(days=3650):
        raise HTTPException(400, detail={"code": "bad_date", "message": "Ngày báo cáo không hợp lệ"})


def _safe_pdf_path(output_dir: str, stored: str | None) -> Path | None:
    """Chỉ phục vụ file PDF nằm trong output_dir."""
    if not stored:
        return None
    path = Path(stored).resolve()
    if Path(output_dir).resolve() not in path.parents or not path.is_file():
        return None
    return path


def _pdf_response(content: bytes | None, path: Path | None, filename: str, inline: bool) -> Response:
    disp = "inline" if inline else "attachment"
    headers = {"Content-Disposition": f'{disp}; filename="{filename}"', "Cache-Control": "no-store"}
    if path is not None:
        return FileResponse(path, media_type="application/pdf", headers=headers)
    return Response(content, media_type="application/pdf", headers=headers)


@router.get("/api/reports/shift")
def shift_report(
    request: Request,
    date_: date = Query(alias="date"),
    shift: str = Query(max_length=32),
    format: Literal["html", "pdf", "json"] = "html",
    download: bool = False,
    user: CurrentUser = Depends(api_user_ready),
) -> Response:
    st = state(request)
    _check_date(date_)
    try:
        payload = reports.get_report(st.engine, st.config, date_, shift)
        window = reports.resolve_window(st.config, date_, shift)
    except ReportError as e:
        raise HTTPException(404, detail={"code": "no_shift", "message": str(e)}) from e
    if format == "json":
        return JSONResponse(payload)
    if format == "html":
        return HTMLResponse(st.renderer.render_html(payload, font_base=f"{st.base}/static/fonts"), headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                                       "font-src 'self'; frame-ancestors 'self'",
            "Cache-Control": "no-store",
        })
    filename = reports.pdf_filename(window)
    snap = reports.get_snapshot(st.engine, date_, shift)
    stored = _safe_pdf_path(st.config.report.output_dir, snap["pdf_path"] if snap else None)
    if stored is not None:
        return _pdf_response(None, stored, filename, not download)
    try:
        content = st.renderer.render_pdf(payload)
    except Exception as e:
        log.exception("report_pdf_failed", extra={"shift": shift, "date": str(date_)})
        raise HTTPException(500, detail={"code": "pdf_failed",
                                         "message": "Không sinh được file PDF. Xem log để biết chi tiết."}) from e
    log.info("report_pdf_rendered", extra={"user": user.username, "shift": shift, "date": str(date_)})
    return _pdf_response(content, None, filename, not download)


@router.get("/api/reports")
def list_reports(request: Request, _: CurrentUser = Depends(api_user_ready)) -> list[dict[str, Any]]:
    st = state(request)
    out = []
    for r in reports.list_snapshots(st.engine):
        out.append({
            "id": r["id"],
            "date": r["report_date"].isoformat(),
            "shift": r["shift_name"],
            "totals": r["totals"],
            "has_pdf": _safe_pdf_path(st.config.report.output_dir, r["pdf_path"]) is not None,
            "generated_at": r["generated_at"].isoformat(),
        })
    return out


class GenerateBody(BaseModel):
    date: date
    shift: str = Field(max_length=32)


@router.post("/api/reports/shift/generate")
def generate_report(body: GenerateBody, request: Request, user: CurrentUser = Depends(api_admin)) -> dict[str, Any]:
    st = state(request)
    _check_date(body.date)
    try:
        window = reports.resolve_window(st.config, body.date, body.shift)
    except ReportError as e:
        raise HTTPException(404, detail={"code": "no_shift", "message": str(e)}) from e
    if not window.is_finished(utcnow()):
        raise HTTPException(409, detail={"code": "shift_running",
                                         "message": "Ca chưa kết thúc, chưa thể chốt báo cáo"})
    payload = reports.finalize_shift(st.engine, st.config, window, generate_pdf=True, renderer=st.renderer,
                                     force=True)
    auth.record(st.engine, user, "report.generate", target=f"{body.shift} {body.date}", ip=client_ip(request))
    snap = reports.get_snapshot(st.engine, body.date, body.shift)
    return {"ok": True, "has_pdf": bool(snap and snap["pdf_path"]), "totals": payload["totals"] if payload else None}


# --------------------------------------------------------------------------- admin


def _user_row(r: dict[str, Any]) -> dict[str, Any]:
    now = utcnow()
    return {
        "id": r["id"], "username": r["username"], "full_name": r["full_name"], "role": r["role"],
        "is_active": r["is_active"], "must_change_password": r["must_change_password"],
        "locked": bool(r["locked_until"] and r["locked_until"] > now),
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "last_login_at": r["last_login_at"].isoformat() if r["last_login_at"] else None,
    }


@router.get("/api/admin/users")
def admin_list_users(request: Request, _: CurrentUser = Depends(api_admin)) -> list[dict[str, Any]]:
    return [_user_row(r) for r in auth.list_users(state(request).engine)]


class CreateUserBody(BaseModel):
    username: str = Field(max_length=64)
    full_name: str = Field("", max_length=200)
    role: Literal["viewer", "admin"] = "viewer"
    password: str = Field(max_length=256)


@router.post("/api/admin/users", status_code=201)
def admin_create_user(body: CreateUserBody, request: Request, user: CurrentUser = Depends(api_admin)) -> dict[str, Any]:
    st = state(request)
    try:
        row = auth.create_user(st.engine, st.config.auth, user, body.username, body.full_name, body.role,
                               body.password, client_ip(request))
    except AuthError as e:
        raise _auth_error(e) from e
    return _user_row(row)


class UpdateUserBody(BaseModel):
    full_name: str | None = Field(None, max_length=200)
    role: Literal["viewer", "admin"] | None = None
    is_active: bool | None = None
    unlock: bool = False


@router.patch("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, body: UpdateUserBody, request: Request,
                      user: CurrentUser = Depends(api_admin)) -> dict[str, Any]:
    st = state(request)
    try:
        row = auth.update_user(st.engine, user, user_id, full_name=body.full_name, role=body.role,
                               is_active=body.is_active, unlock=body.unlock, ip=client_ip(request))
    except AuthError as e:
        raise _auth_error(e) from e
    return _user_row(row)


class ResetPasswordBody(BaseModel):
    new_password: str = Field(max_length=256)


@router.post("/api/admin/users/{user_id}/reset-password")
def admin_reset_password(user_id: int, body: ResetPasswordBody, request: Request,
                         user: CurrentUser = Depends(api_admin)) -> dict[str, Any]:
    st = state(request)
    try:
        auth.reset_password(st.engine, st.config.auth, user, user_id, body.new_password, client_ip(request))
    except AuthError as e:
        raise _auth_error(e) from e
    return {"ok": True}


@router.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, request: Request, user: CurrentUser = Depends(api_admin)) -> dict[str, Any]:
    st = state(request)
    try:
        auth.delete_user(st.engine, user, user_id, client_ip(request))
    except AuthError as e:
        raise _auth_error(e) from e
    return {"ok": True}


@router.get("/api/admin/system")
def admin_system(request: Request, _: CurrentUser = Depends(api_admin)) -> dict[str, Any]:
    st = state(request)
    jobs = []
    for j in st.scheduler.get_jobs():
        nrt: datetime | None = getattr(j, "next_run_time", None)
        jobs.append({"id": j.id, "next_run_time": nrt.isoformat() if nrt else None})
    return {
        "version": __version__,
        "config_path": str(st.cfgm.path),
        "config": st.config.redacted(),
        "last_reload_error": st.cfgm.last_reload_error,
        "collector": st.collector.status.as_dict(),
        "jobs": jobs,
    }


@router.post("/api/admin/config/reload")
def admin_reload(request: Request, user: CurrentUser = Depends(api_admin)) -> dict[str, Any]:
    st = state(request)
    try:
        st.cfgm.reload()
    except ConfigError as e:
        auth.record(st.engine, user, "config.reload_failed", ip=client_ip(request))
        raise HTTPException(400, detail={"code": "config_invalid", "message": str(e)}) from e
    auth.record(st.engine, user, "config.reload", ip=client_ip(request))
    return {"ok": True, "message": "Đã nạp lại cấu hình"}


@router.get("/api/admin/audit")
def admin_audit(request: Request, _: CurrentUser = Depends(api_admin)) -> list[dict[str, Any]]:
    rows = auth.list_audit(state(request).engine)
    return [
        {"id": r["id"], "username": r["username"], "action": r["action"], "target": r["target"],
         "detail": r["detail"], "ip": r["ip"], "created_at": r["created_at"].isoformat()}
        for r in rows
    ]
