"""Tính số liệu báo cáo cuối ca (hàm thuần, không phụ thuộc DB).

Quy tắc (khoảng ca là nửa mở [start, end)):
- Phát sinh       : occurrence có starts_at trong ca.
- Đã clear        : occurrence có resolved_at trong ca (kể cả phát sinh từ ca trước).
- Tồn sang ca sau : occurrence có starts_at < end và (chưa clear hoặc resolved_at >= end).
                    Chỉ chốt khi ca đã kết thúc (chưa kết thúc => None, hiển thị "--").
- Bàn giao        : occurrence còn active tại mốc tham chiếu (end, hoặc now nếu ca đang chạy)
                    và thời gian tồn > ngưỡng; gộp theo application + alertname.
- Top N           : đếm trên tập "Phát sinh".
- Xu thế          : số phát sinh theo ngày lịch (giờ địa phương), N ngày kết thúc ở ngày
                    kết thúc ca; ngày cuối chỉ tính tới thời điểm kết thúc ca.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..alerts_query import group_summary
from ..timeutil import ShiftWindow, format_duration, local_day_bounds


@dataclass(frozen=True)
class Occ:
    starts_at: datetime
    resolved_at: datetime | None
    alertname: str
    application: str | None
    summary: str = ""
    instance: str | None = None
    fingerprint: str = ""


def started_in(occs: list[Occ], start: datetime, end: datetime) -> list[Occ]:
    return [o for o in occs if start <= o.starts_at < end]


def resolved_in(occs: list[Occ], start: datetime, end: datetime) -> list[Occ]:
    return [o for o in occs if o.resolved_at is not None and start <= o.resolved_at < end]


def active_at(occs: list[Occ], t: datetime) -> list[Occ]:
    return [o for o in occs if o.starts_at < t and (o.resolved_at is None or o.resolved_at >= t)]


def shift_totals(occs: list[Occ], w: ShiftWindow, now: datetime) -> dict[str, int | None]:
    return {
        "new": len(started_in(occs, w.start, w.end)),
        "cleared": len(resolved_in(occs, w.start, w.end)),
        "carried_over": len(active_at(occs, w.end)) if w.is_finished(now) else None,
    }


def top_n(values: list[str], n: int, total: int | None = None) -> list[dict[str, Any]]:
    total = len(values) if total is None else total
    counts = Counter(values).most_common()
    # Thứ tự ổn định: số lượng giảm dần, cùng số lượng thì theo tên
    counts.sort(key=lambda kv: (-kv[1], kv[0]))
    top = counts[:n]
    max_count = top[0][1] if top else 0
    return [
        {
            "rank": i,
            "name": name,
            "count": c,
            "total": total,
            "pct": round(c * 100 / total, 1) if total else 0.0,
            "bar_pct": round(c * 100 / max_count, 1) if max_count else 0.0,
        }
        for i, (name, c) in enumerate(top, 1)
    ]


def compare(today: int | None, yesterday: int | None) -> dict[str, Any]:
    if today is None:
        return {"value": None, "yesterday": yesterday, "delta": None, "trend": "none", "bar_pct": 0.0}
    y = yesterday or 0
    delta = today - y
    peak = max(today, y)
    return {
        "value": today,
        "yesterday": y,
        "delta": delta,
        "trend": "up" if delta > 0 else "down" if delta < 0 else "flat",
        "bar_pct": round(today * 100 / peak, 1) if peak else 0.0,
    }


def handover(
    occs: list[Occ], ref: datetime, threshold_hours: float, missing_text: str, limit: int,
    tz: ZoneInfo | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Nhóm cảnh báo tồn quá ngưỡng, sắp xếp theo thời gian tồn GIẢM DẦN (giữ như mẫu).

    "Bắt đầu" của nhóm = cảnh báo bắt đầu sớm nhất (khớp với thời gian tồn hiển thị)."""
    threshold = timedelta(hours=threshold_hours)
    long_running = [o for o in active_at(occs, ref) if ref - o.starts_at > threshold]
    groups: dict[tuple[str, str], list[Occ]] = {}
    for o in long_running:
        groups.setdefault((o.application or missing_text, o.alertname), []).append(o)
    items = []
    for (app, alertname), members in groups.items():
        secs = max((ref - m.starts_at).total_seconds() for m in members)
        first = min(m.starts_at for m in members)
        summaries = [m.summary or m.alertname for m in members]
        items.append(
            {
                "application": app,
                "alertname": alertname,
                "summary": group_summary(summaries, [m.instance or "" for m in members], len(members)),
                "count": len(members),
                "duration_seconds": secs,
                "duration": format_duration(secs),
                "starts_at": first.isoformat(),
                "start_label": first.astimezone(tz).strftime("%d/%m/%Y %H:%M:%S") if tz else
                first.strftime("%d/%m/%Y %H:%M:%S"),
            }
        )
    items.sort(key=lambda x: (-x["duration_seconds"], x["application"], x["alertname"]))
    return items[:limit], max(0, len(items) - limit)


def trend(occs: list[Occ], last_day: date, days: int, cap: datetime, tz: ZoneInfo) -> list[dict[str, Any]]:
    out = []
    for i in range(days - 1, -1, -1):
        d = last_day - timedelta(days=i)
        start, end = local_day_bounds(d, tz)
        end = min(end, cap)
        count = len(started_in(occs, start, end)) if end > start else 0
        out.append({"date": d.isoformat(), "label": d.strftime("%d/%m"), "count": count})
    return out


def trend_last_day(w: ShiftWindow, tz: ZoneInfo) -> date:
    # end là mốc loại trừ; lùi 1 micro giây để ca kết thúc lúc 00:00 không nhảy sang ngày sau
    return (w.end - timedelta(microseconds=1)).astimezone(tz).date()


def data_range(w: ShiftWindow, yesterday: ShiftWindow, trend_days: int, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Khoảng thời gian cần lấy occurrence để tính toàn bộ báo cáo."""
    first_trend_day = trend_last_day(w, tz) - timedelta(days=trend_days - 1)
    trend_start, _ = local_day_bounds(first_trend_day, tz)
    return min(yesterday.start, trend_start), w.end


def build_report(
    *,
    occs: list[Occ],
    window: ShiftWindow,
    yesterday: ShiftWindow,
    now: datetime,
    tz: ZoneInfo,
    title: str,
    top_count: int,
    trend_days: int,
    handover_threshold_hours: float,
    handover_limit: int,
    missing_text: str,
    yesterday_carried_override: int | None = None,
) -> dict[str, Any]:
    finished = window.is_finished(now)
    totals = shift_totals(occs, window, now)
    y_totals = shift_totals(occs, yesterday, now)
    if yesterday_carried_override is not None:
        y_totals["carried_over"] = yesterday_carried_override

    new_occs = started_in(occs, window.start, window.end)
    total_new = len(new_occs)
    ref = window.end if finished else min(now, window.end)
    handover_items, handover_more = handover(occs, ref, handover_threshold_hours, missing_text, handover_limit, tz)

    start_local = window.start.astimezone(tz)
    end_local = window.end.astimezone(tz)
    return {
        "title": title,
        "report_date": window.report_date.isoformat(),
        "report_date_label": window.report_date.strftime("%d/%m/%Y"),
        "shift_name": window.name,
        "shift_label": window.shift.label,
        "period_start": window.start.isoformat(),
        "period_end": window.end.isoformat(),
        "period_label": f"{start_local:%H:%M %d/%m/%Y} → {end_local:%H:%M %d/%m/%Y}",
        "finished": finished,
        "generated_at": now.isoformat(),
        "generated_at_label": now.astimezone(tz).strftime("%H:%M:%S %d/%m/%Y"),
        "totals": totals,
        "compare": {
            "new": compare(totals["new"], y_totals["new"]),
            "carried_over": compare(totals["carried_over"], y_totals["carried_over"]),
        },
        "handover": handover_items,
        "handover_more": handover_more,
        "handover_threshold_hours": handover_threshold_hours,
        "top_applications": top_n([o.application or missing_text for o in new_occs], top_count, total_new),
        "top_alertnames": top_n([o.alertname for o in new_occs], top_count, total_new),
        "trend": trend(occs, trend_last_day(window, tz), trend_days, window.end, tz),
    }
