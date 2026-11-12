"""Điểm khởi động ứng dụng TrucCa."""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import delete, text
from sqlalchemy.engine import Engine
from starlette.exceptions import HTTPException

from . import __version__
from .auth import service as auth
from .collector import Collector
from .config import AppConfig, ConfigError, ConfigManager
from .db import alerts, audit_log, init_engine, occurrences, run_migrations, shift_snapshots
from .logging_setup import setup_logging
from .reports.render import ReportRenderer
from .reports.service import finalize_due_shifts
from .timeutil import utcnow
from .web import api, pages
from .web.deps import PageRedirect

log = logging.getLogger("trucca")

PKG_DIR = Path(__file__).resolve().parent


@dataclass
class AppState:
    cfgm: ConfigManager
    engine: Engine
    collector: Collector
    renderer: ReportRenderer
    scheduler: BackgroundScheduler
    templates: Environment
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def config(self) -> AppConfig:
        return self.cfgm.config

    @property
    def base(self) -> str:
        bp = self.config.server.base_path
        return "" if bp == "/" else bp


# --------------------------------------------------------------------------- jobs


def retention_job(st: AppState) -> None:
    cfg = st.config
    cutoff = utcnow() - timedelta(days=cfg.retention_days)
    with st.engine.begin() as conn:
        occ = conn.execute(delete(occurrences).where(occurrences.c.resolved_at < cutoff)).rowcount
        al = conn.execute(delete(alerts).where(alerts.c.status == "resolved", alerts.c.resolved_at < cutoff)).rowcount
        snaps = conn.execute(
            delete(shift_snapshots).where(shift_snapshots.c.period_end < cutoff).returning(shift_snapshots.c.pdf_path)
        ).scalars().all()
        aud = conn.execute(delete(audit_log).where(audit_log.c.created_at < cutoff)).rowcount
    out_dir = Path(cfg.report.output_dir).resolve()
    removed = 0
    for p in snaps:
        if not p:
            continue
        path = Path(p).resolve()
        if out_dir in path.parents and path.is_file():  # chỉ xoá file nằm trong output_dir
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                log.warning("retention_unlink_failed", extra={"path": str(path)})
    sessions_purged = auth.purge_expired_sessions(st.engine)
    log.info("retention_done", extra={"occurrences": occ, "alerts": al, "snapshots": len(snaps), "pdf_files": removed,
                                      "audit": aud, "sessions": sessions_purged})


def safe(fn: Any, name: str) -> Any:
    def run(*a: Any, **kw: Any) -> None:
        try:
            fn(*a, **kw)
        except Exception:
            log.exception("job_failed", extra={"job": name})
    return run


def schedule_jobs(st: AppState) -> None:
    sch = st.scheduler
    cfg = st.config
    sch.add_job(safe(st.collector.poll_once, "poll"), "interval", seconds=cfg.alertmanager.poll_interval_seconds,
                id="poll", max_instances=1, coalesce=True, next_run_time=utcnow(), replace_existing=True)
    sch.add_job(safe(lambda: finalize_due_shifts(st.engine, st.config, st.renderer,
                                                 last_success_at=st.collector.status.last_success_at),
                     "finalize_shifts"),
                "interval", seconds=30, id="finalize_shifts", max_instances=1, coalesce=True,
                next_run_time=utcnow() + timedelta(seconds=20), replace_existing=True)
    sch.add_job(safe(st.cfgm.check_for_changes, "config_watch"), "interval", seconds=10, id="config_watch",
                max_instances=1, coalesce=True, replace_existing=True)
    sch.add_job(safe(lambda: retention_job(st), "retention"), "interval", hours=6, id="retention",
                max_instances=1, coalesce=True, next_run_time=utcnow() + timedelta(minutes=2), replace_existing=True)


def on_config_change(st: AppState) -> Any:
    def handler(old: AppConfig, new: AppConfig) -> None:
        logging.getLogger().setLevel(new.log_level)
        if old.alertmanager != new.alertmanager or old.labels != new.labels or old.filters != new.filters:
            st.collector.update_config(new)
        else:
            st.collector.cfg = new
        if old.alertmanager.poll_interval_seconds != new.alertmanager.poll_interval_seconds:
            st.scheduler.reschedule_job("poll", trigger="interval", seconds=new.alertmanager.poll_interval_seconds)
        st.renderer.pdf_timeout_ms = new.report.pdf_timeout_seconds * 1000
        for key in ("database", "server"):
            if getattr(old, key) != getattr(new, key):
                log.warning("config_change_requires_restart", extra={"section": key})
    return handler


def wait_for_db(engine: Engine, timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as e:
            if time.monotonic() > deadline:
                raise RuntimeError(f"Không kết nối được PostgreSQL sau {timeout}s: {type(e).__name__}") from e
            log.warning("db_not_ready", extra={"error": type(e).__name__})
            time.sleep(3)


# --------------------------------------------------------------------------- app


def check_output_dir(path: str) -> None:
    """Báo lỗi rõ ràng nếu thư mục báo cáo không ghi được (thường do quyền của volume)."""
    out = Path(path)
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".write_test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        raise RuntimeError(
            f"Không ghi được vào thư mục báo cáo {out} ({e.strerror}). Container chạy với UID {os.getuid()}; "
            "hãy cấp quyền cho thư mục volume trên host, ví dụ: "
            f"sudo chown -R {os.getuid()}:{os.getgid()} ./data/trucca"
        ) from e


def build_state(cfgm: ConfigManager) -> AppState:
    cfg = cfgm.config
    db_url = cfg.database.url.get_secret_value()
    engine = init_engine(db_url, cfg.database.pool_size)
    wait_for_db(engine)
    run_migrations(db_url)
    auth.ensure_bootstrap_admin(engine, os.environ.get("TRUCCA_ADMIN_USERNAME"),
                                os.environ.get("TRUCCA_ADMIN_PASSWORD"), cfg.auth.min_password_length)
    check_output_dir(cfg.report.output_dir)
    templates = Environment(loader=FileSystemLoader(str(PKG_DIR / "templates")),
                            autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
    st = AppState(
        cfgm=cfgm, engine=engine, collector=Collector(engine, cfg),
        renderer=ReportRenderer(cfg.report.pdf_timeout_seconds),
        scheduler=BackgroundScheduler(timezone="UTC", job_defaults={"misfire_grace_time": 30}),
        templates=templates,
    )
    cfgm.on_change(on_config_change(st))
    return st


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "SAMEORIGIN",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
DEFAULT_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
               "font-src 'self'; frame-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; "
               "form-action 'self'")


def create_app(cfgm: ConfigManager | None = None) -> FastAPI:
    cfgm = cfgm or ConfigManager()
    base = "" if cfgm.config.server.base_path == "/" else cfgm.config.server.base_path

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        st = build_state(cfgm)
        app.state.trucca = st
        schedule_jobs(st)
        st.scheduler.start()
        log.info("trucca_started", extra={"version": __version__, "base_path": base or "/",
                                          "alertmanager": st.config.alertmanager.url})
        try:
            yield
        finally:
            st.scheduler.shutdown(wait=False)
            st.collector.client.close()
            st.engine.dispose()
            log.info("trucca_stopped")

    app = FastAPI(title="TrucCa", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        resp: Response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        resp.headers.setdefault("Content-Security-Policy", DEFAULT_CSP)
        return resp

    @app.exception_handler(PageRedirect)
    async def page_redirect(request: Request, exc: PageRedirect) -> Response:
        return RedirectResponse(f"{base}{exc.path}", status_code=303)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        if exc.status_code == 404 and detail.get("message") == "Not Found":
            detail = {"code": "not_found", "message": "Không tìm thấy trang hoặc tài nguyên"}
        elif exc.status_code == 405:
            detail = {"code": "method_not_allowed", "message": "Phương thức không được hỗ trợ"}
        accept = request.headers.get("accept", "")
        if "text/html" in accept and not request.url.path.startswith(f"{base}/api"):
            html = (f'<!doctype html><meta charset="utf-8"><title>Lỗi {exc.status_code}</title>'
                    f'<link rel="stylesheet" href="{base}/static/css/app.css">'
                    f'<main class="error-page"><h1>{exc.status_code}</h1><p>{_esc(detail["message"])}</p>'
                    f'<a class="btn" href="{base}/">Về trang cảnh báo</a></main>')
            return HTMLResponse(html, status_code=exc.status_code)
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> Response:
        fields = [".".join(str(p) for p in e["loc"] if p not in ("body", "query")) for e in exc.errors()]
        return JSONResponse({"error": {"code": "validation", "message": "Dữ liệu gửi lên không hợp lệ",
                                       "fields": fields}}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> Response:
        log.exception("unhandled_error", extra={"path": request.url.path})
        return JSONResponse({"error": {"code": "internal", "message": "Lỗi hệ thống, vui lòng thử lại hoặc xem log"}},
                            status_code=500)

    @app.get(f"{base}/healthz", include_in_schema=False)
    def healthz(request: Request) -> JSONResponse:
        st: AppState = request.app.state.trucca
        try:
            with st.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False
        col = st.collector.status.as_dict()
        body = {"status": "ok" if db_ok else "error", "db": db_ok,
                "alertmanager": "ok" if col["connected"] else "degraded", "version": __version__}
        return JSONResponse(body, status_code=200 if db_ok else 503)

    app.include_router(api.router, prefix=base)
    app.include_router(pages.router, prefix=base)
    app.mount(f"{base}/static", StaticFiles(directory=str(PKG_DIR / "static")), name="static")
    if base:
        @app.get("/", include_in_schema=False)
        def root_redirect() -> Response:
            return RedirectResponse(f"{base}/", status_code=307)
    return app


def _esc(s: str) -> str:
    from markupsafe import escape
    return str(escape(s))


def main() -> None:
    import uvicorn

    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    try:
        cfgm = ConfigManager()
    except ConfigError as e:
        log.critical("config_invalid", extra={"error": str(e)})
        print(f"\n[TrucCa] {e}\n", file=sys.stderr)
        sys.exit(2)
    setup_logging(cfgm.config.log_level)
    app = create_app(cfgm)
    uvicorn.run(app, host=cfgm.config.server.host, port=cfgm.config.server.port, log_config=None,
                proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))


if __name__ == "__main__":
    main()
