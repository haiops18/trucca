"""Dịch vụ báo cáo ca: lấy dữ liệu, snapshot khi hết ca, sinh PDF."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from ..config import AppConfig
from ..db import occurrences, shift_snapshots
from ..timeutil import ShiftWindow, finished_shifts_between, shift_window, utcnow
from .calc import Occ, build_report, data_range

log = logging.getLogger(__name__)

# Ca kết thúc quá lâu thì không tự sinh bù khi khởi động (tránh sinh hàng loạt)
CATCH_UP_WINDOW = timedelta(days=2)


class ReportError(Exception):
    pass


def load_occurrences(engine: Engine, start: datetime, end: datetime) -> list[Occ]:
    """Occurrence có giao với [start, end) hoặc bắt đầu trong khoảng đó."""
    stmt = select(
        occurrences.c.starts_at, occurrences.c.resolved_at, occurrences.c.alertname, occurrences.c.application,
        occurrences.c.summary, occurrences.c.instance, occurrences.c.fingerprint,
    ).where(
        and_(
            occurrences.c.starts_at < end,
            or_(occurrences.c.resolved_at.is_(None), occurrences.c.resolved_at >= start),
            # Không tính các occurrence bị đóng vì không còn khớp filter
            or_(occurrences.c.resolve_source.is_(None), occurrences.c.resolve_source != "filtered_out"),
        )
    )
    with engine.connect() as conn:
        return [Occ(*r) for r in conn.execute(stmt)]


def get_snapshot(engine: Engine, report_date: date, shift_name: str) -> dict[str, Any] | None:
    stmt = select(shift_snapshots).where(
        shift_snapshots.c.report_date == report_date, shift_snapshots.c.shift_name == shift_name
    )
    with engine.connect() as conn:
        row = conn.execute(stmt).first()
    return dict(row._mapping) if row else None


def resolve_window(cfg: AppConfig, report_date: date, shift_name: str) -> ShiftWindow:
    shift = cfg.shift(shift_name)
    if shift is None:
        raise ReportError(f"Không có ca '{shift_name}' trong cấu hình")
    return shift_window(shift, report_date, cfg.tz)


def compute_report(engine: Engine, cfg: AppConfig, window: ShiftWindow, now: datetime) -> dict[str, Any]:
    tz = cfg.tz
    yesterday = shift_window(window.shift, window.report_date - timedelta(days=1), tz)
    start, end = data_range(window, yesterday, cfg.report.trend_days, tz)
    occs = load_occurrences(engine, start, end)
    y_snap = get_snapshot(engine, yesterday.report_date, yesterday.name)
    return build_report(
        occs=occs,
        window=window,
        yesterday=yesterday,
        now=now,
        tz=tz,
        title=cfg.report.title,
        top_count=cfg.report.top_n,
        trend_days=cfg.report.trend_days,
        handover_threshold_hours=cfg.report.handover_threshold_hours,
        handover_limit=cfg.report.handover_max_items,
        missing_text=cfg.display.missing_label_text,
        yesterday_carried_override=y_snap["carried_over"] if y_snap else None,
    )


def get_report(engine: Engine, cfg: AppConfig, report_date: date, shift_name: str) -> dict[str, Any]:
    """Ca đã chốt => trả snapshot (số liệu cố định). Ngược lại tính trực tiếp."""
    window = resolve_window(cfg, report_date, shift_name)
    snap = get_snapshot(engine, report_date, shift_name)
    if snap:
        payload = dict(snap["payload"])
        payload["from_snapshot"] = True
        payload["snapshot_id"] = snap["id"]
        payload["has_pdf"] = bool(snap["pdf_path"])
        return payload
    payload = compute_report(engine, cfg, window, utcnow())
    payload["from_snapshot"] = False
    payload["has_pdf"] = False
    return payload


def save_snapshot(engine: Engine, window: ShiftWindow, payload: dict[str, Any], pdf_path: str | None) -> int:
    values = {
        "report_date": window.report_date,
        "shift_name": window.name,
        "period_start": window.start,
        "period_end": window.end,
        "carried_over": payload["totals"]["carried_over"] or 0,
        "payload": payload,
        "pdf_path": pdf_path,
        "generated_at": utcnow(),
    }
    stmt = pg_insert(shift_snapshots).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_snapshot_date_shift",
        set_={k: values[k] for k in ("payload", "carried_over", "pdf_path", "generated_at")},
    ).returning(shift_snapshots.c.id)
    with engine.begin() as conn:
        return conn.execute(stmt).scalar_one()


def pdf_filename(window: ShiftWindow) -> str:
    return f"noc_shift_report_{window.name}_{window.report_date:%Y%m%d}.pdf"


_finalize_lock = threading.Lock()
# Đếm số lần sinh PDF thất bại theo ca để không thử lại vô hạn mỗi phút
_pdf_failures: dict[tuple[date, str], int] = {}
MAX_PDF_RETRIES = 3


def finalize_shift(engine: Engine, cfg: AppConfig, window: ShiftWindow, *, generate_pdf: bool,
                   renderer: Any = None, force: bool = False) -> dict[str, Any] | None:
    """Chốt số liệu ca đã kết thúc và (tuỳ chọn) sinh PDF. Idempotent."""
    with _finalize_lock:
        snap = get_snapshot(engine, window.report_date, window.name)
        key = (window.report_date, window.name)
        if snap and not force and (snap["pdf_path"] or not generate_pdf
                                   or _pdf_failures.get(key, 0) >= MAX_PDF_RETRIES):
            return None
        if snap and not force:
            payload = snap["payload"]  # giữ số liệu đã chốt, chỉ bổ sung PDF
        else:
            # Tính tại đúng mốc kết thúc ca để "tồn sang ca sau" chính xác
            payload = compute_report(engine, cfg, window, max(utcnow(), window.end))
            payload["generated_at"] = utcnow().isoformat()
        pdf_path = snap["pdf_path"] if snap else None
        if generate_pdf and renderer is not None:
            out_dir = Path(cfg.report.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            target = out_dir / pdf_filename(window)
            try:
                target.write_bytes(renderer.render_pdf(payload))
                pdf_path = str(target)
                _pdf_failures.pop(key, None)
            except Exception:
                _pdf_failures[key] = _pdf_failures.get(key, 0) + 1
                log.exception("report_pdf_failed", extra={"shift": window.name, "date": str(window.report_date)})
        sid = save_snapshot(engine, window, payload, pdf_path)
        log.info("shift_finalized", extra={"shift": window.name, "date": str(window.report_date),
                                           "snapshot_id": sid, "pdf": pdf_path,
                                           "carried_over": payload["totals"]["carried_over"]})
        return payload


def first_collection_time(engine: Engine) -> datetime | None:
    """Thời điểm TrucCa ghi nhận cảnh báo đầu tiên (xấp xỉ lúc bắt đầu thu thập)."""
    with engine.connect() as conn:
        return conn.execute(select(func.min(occurrences.c.first_seen_at))).scalar()


# Nếu Alertmanager mất kết nối lúc hết ca, chờ tối đa chừng này rồi vẫn chốt (đánh dấu dữ liệu có thể thiếu)
STALE_FALLBACK = timedelta(minutes=30)


def finalize_grace(cfg: AppConfig) -> timedelta:
    """Chờ sau giờ kết thúc ca để collector kịp xác nhận các cảnh báo vừa clear sát giờ."""
    am = cfg.alertmanager
    return timedelta(seconds=(am.resolve_after_missing_polls + 1) * am.poll_interval_seconds)


def finalize_due_shifts(engine: Engine, cfg: AppConfig, renderer: Any, now: datetime | None = None,
                        last_success_at: datetime | None = None) -> int:
    """Chốt các ca đã kết thúc (kể cả ca bị lỡ trong CATCH_UP_WINDOW do container tắt).

    Chỉ chốt khi đã qua thời gian chờ ``finalize_grace`` VÀ có lần poll thành công sau mốc đó;
    nếu Alertmanager mất kết nối quá ``STALE_FALLBACK`` thì vẫn chốt để không thiếu báo cáo.
    """
    now = now or utcnow()
    grace = finalize_grace(cfg)
    done = 0
    collecting_since = first_collection_time(engine)
    if collecting_since is None:
        return 0  # chưa thu thập được gì: không chốt báo cáo rỗng
    for w in finished_shifts_between(cfg.shifts, now - CATCH_UP_WINDOW, now, cfg.tz):
        if w.end <= collecting_since:
            # Ca kết thúc trước khi TrucCa bắt đầu thu thập => số liệu không đáng tin, không tự chốt.
            # Vẫn xem được (tính trực tiếp) hoặc admin chốt thủ công.
            continue
        ready_at = w.end + grace
        if now < ready_at:
            continue
        fresh = last_success_at is not None and last_success_at >= ready_at
        if not fresh and now < w.end + STALE_FALLBACK:
            continue  # chờ collector lấy được dữ liệu mới sau giờ kết thúc ca
        if not fresh:
            log.warning("shift_finalized_with_stale_data", extra={"shift": w.name, "date": str(w.report_date)})
        if finalize_shift(engine, cfg, w, generate_pdf=cfg.report.auto_generate_at_shift_end, renderer=renderer):
            done += 1
    return done


def list_snapshots(engine: Engine, limit: int = 100) -> list[dict[str, Any]]:
    stmt = (
        select(shift_snapshots.c.id, shift_snapshots.c.report_date, shift_snapshots.c.shift_name,
               shift_snapshots.c.carried_over, shift_snapshots.c.pdf_path, shift_snapshots.c.generated_at,
               shift_snapshots.c.payload["totals"].label("totals"))
        .order_by(shift_snapshots.c.period_end.desc())
        .limit(limit)
    )
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(stmt)]
