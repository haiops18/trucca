"""Endpoint JSON nội bộ cho giao diện (yêu cầu đăng nhập; không dành cho hệ thống ngoài)."""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..alerts_query import (
    DEFAULT_ORDER,
    DEFAULT_SORT,
    AlertFilter,
    age_buckets,
    fetch_active,
    fetch_at,
    filter_age,
    filter_options,
    format_start,
    group_rows,
    present_rows,
    severity_counts,
    strip_internal,
)
from ..auth import service as auth
from ..auth.service import AuthError, CurrentUser
from ..config import ConfigError
from ..export_xlsx import build_alerts_xlsx
from ..reports import service as reports
from ..reports.service import ReportError
from ..timeutil import current_shift, utcnow
from .deps import (
    PASSWORD_PAGE,
    SESSION_COOKIE,
    api_admin,
    api_user,
    api_user_ready,
    client_ip,
    set_session_cookie,
    state,
)

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
                         "redirect": f"{st.base}{PASSWORD_PAGE}" if user.must_change_password else f"{st.base}/"})
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


@router.post("/api/admin/password")
def change_password(body: PasswordBody, request: Request, user: CurrentUser = Depends(api_user)) -> dict[str, Any]:
    """Module Quản trị > Đổi mật khẩu: MỌI người dùng đã đăng nhập đổi mật khẩu của CHÍNH MÌNH
    (yêu cầu mật khẩu hiện tại, kiểm tra độ mạnh; không nhận user_id nên không đổi được của người khác)."""
    return _change_own_password(body, request, user)


@router.post("/api/account/password", deprecated=True)
def change_password_legacy(body: PasswordBody, request: Request, response: Response,
                           user: CurrentUser = Depends(api_user)) -> dict[str, Any]:
    """[Deprecated] Giữ cho client cũ; dùng POST /api/admin/password. Hành vi giống hệt."""
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = f'<{state(request).base}/api/admin/password>; rel="successor-version"'
    return _change_own_password(body, request, user)


def _change_own_password(body: PasswordBody, request: Request, user: CurrentUser) -> dict[str, Any]:
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


AT_PARAM = Query("", max_length=40, description="Thời điểm tra cứu (ISO, giờ theo timezone cấu hình). "
                                                "Bỏ trống = cảnh báo đang diễn ra.")


def parse_at(raw: str, tz: Any, now: datetime) -> datetime | None:
    """Tham số `at` -> datetime UTC. Chuỗi không có múi giờ hiểu theo timezone trong config.

    Bỏ trống -> None (xem cảnh báo đang diễn ra). Thời điểm ở tương lai -> lỗi 400."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        at = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        raise HTTPException(400, detail={"code": "invalid_at",
                                         "message": "Thời điểm không hợp lệ (dạng 2026-11-12T08:30)"}) from None
    if at.tzinfo is None:
        at = at.replace(tzinfo=tz)
    at = at.astimezone(UTC)
    if at > now + timedelta(minutes=1):
        raise HTTPException(400, detail={"code": "future_at", "message": "Không thể chọn thời điểm ở tương lai"})
    return at


def _rows(request: Request, f: AlertFilter, group: bool, min_hours: float | None, max_hours: float | None,
          with_buckets: bool = False, at: datetime | None = None,
          ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Trả về (dòng hiển thị, nhóm tuổi). Nhóm tuổi tính trên tập đã lọc, TRƯỚC khi lọc theo tuổi.

    `at` = None: cảnh báo đang diễn ra; có `at`: cảnh báo đang diễn ra tại thời điểm đó (thời gian tồn tính tới `at`,
    kèm trạng thái hiện tại). Dùng chung cho bảng web và Excel, nên Excel xuất đúng thứ tự đang hiển thị.
    """
    st = state(request)
    ref = at or utcnow()
    f = f.normalized()
    raw = fetch_at(st.engine, f, at) if at else fetch_active(st.engine, f)
    buckets = age_buckets(raw, ref, st.config.report.handover_threshold_hours) if with_buckets else []
    raw = filter_age(raw, ref, min_hours, max_hours)
    missing = st.config.display.missing_label_text
    tz = st.config.tz
    rows = (group_rows(raw, ref, missing, tz, f.sort, f.order) if group
            else present_rows(raw, ref, missing, tz))
    return rows, buckets


def _severity_counts(request: Request, f: AlertFilter, min_hours: float | None,
                     max_hours: float | None, at: datetime | None = None) -> dict[str, int]:
    """Đếm theo mức độ với các bộ lọc khác đang áp dụng (bỏ qua chính bộ lọc mức độ)."""
    engine = state(request).engine
    f2 = replace(f.normalized(), severity="")
    raw = fetch_at(engine, f2, at) if at else fetch_active(engine, f2)
    return severity_counts(filter_age(raw, at or utcnow(), min_hours, max_hours))


@router.get("/api/alerts")
def list_alerts(
    request: Request,
    q: str = Query("", max_length=200),
    application: str = Query("", max_length=500),
    owner: str = Query("", max_length=500),
    alertname: str = Query("", max_length=500),
    severity: str = Query("", max_length=100),
    sort: str = DEFAULT_SORT,
    order: str = DEFAULT_ORDER,
    group: bool = False,
    min_hours: float | None = Query(None, ge=0),
    max_hours: float | None = Query(None, gt=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=1000),
    at: str = AT_PARAM,
    _: CurrentUser = Depends(api_user_ready),
) -> dict[str, Any]:
    st = state(request)
    now = utcnow()
    at_dt = parse_at(at, st.config.tz, now)
    f = _alert_filter(q, application, owner, alertname, severity, sort, order)
    rows, buckets = _rows(request, f, group, min_hours, max_hours, with_buckets=True, at=at_dt)
    total = len(rows)
    pages = max(1, math.ceil(total / page_size))
    page = min(page, pages)
    items = strip_internal(rows[(page - 1) * page_size: page * page_size])
    out: dict[str, Any] = {
        "mode": "history" if at_dt else "live",
        "items": items,
        "sort": f.normalized().sort,
        "order": f.normalized().order,
        "severity_counts": _severity_counts(request, f, min_hours, max_hours, at_dt),
        "total": total,
        "total_alerts": sum(r["count"] for r in rows),
        "page": page,
        "pages": pages,
        "page_size": page_size,
        "age_buckets": buckets,
        "handover_threshold_hours": st.config.report.handover_threshold_hours,
        "generated_at": now.isoformat(),
        "collector": st.collector.status.as_dict(),
    }
    if at_dt:
        since = reports.first_collection_time(st.engine)
        out.update({
            "at": at_dt.isoformat(),
            "at_display": format_start(at_dt, st.config.tz),
            # Dữ liệu lịch sử chỉ có từ khi TrucCa bắt đầu thu thập và trong retention_days
            "data_since": since.isoformat() if since else None,
            "data_since_display": format_start(since, st.config.tz) if since else "",
            "before_data": since is None or at_dt < since,
            "retention_days": st.config.retention_days,
        })
    return out


@router.get("/api/alerts/filters")
def alert_filters(request: Request, at: str = AT_PARAM,
                  _: CurrentUser = Depends(api_user_ready)) -> dict[str, list[str]]:
    st = state(request)
    return filter_options(st.engine, parse_at(at, st.config.tz, utcnow()))


@router.get("/api/alerts/export.xlsx")
def export_alerts(
    request: Request,
    q: str = Query("", max_length=200),
    application: str = Query("", max_length=500),
    owner: str = Query("", max_length=500),
    alertname: str = Query("", max_length=500),
    severity: str = Query("", max_length=100),
    sort: str = DEFAULT_SORT,
    order: str = DEFAULT_ORDER,
    group: bool = False,
    min_hours: float | None = Query(None, ge=0),
    max_hours: float | None = Query(None, gt=0),
    at: str = AT_PARAM,
    user: CurrentUser = Depends(api_user_ready),
) -> Response:
    st = state(request)
    now = utcnow()
    at_dt = parse_at(at, st.config.tz, now)
    f = _alert_filter(q, application, owner, alertname, severity, sort, order)
    rows, _ = _rows(request, f, group, min_hours, max_hours, at=at_dt)
    content = build_alerts_xlsx(rows, now, tz=st.config.tz, at=at_dt)
    if at_dt:
        filename = f"canh-bao-tai-thoi-diem_{at_dt.astimezone(st.config.tz):%Y%m%d_%H%M}.xlsx"
    else:
        filename = f"canh-bao-dang-dien-ra_{now.astimezone(st.config.tz):%Y%m%d_%H%M}.xlsx"
    log.info("alerts_exported", extra={"user": user.username, "rows": len(rows),
                                       "at": at_dt.isoformat() if at_dt else None})
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
