"""Tiện ích thời gian: định dạng thời gian tồn, tính khung giờ ca."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import ShiftConfig

UTC = UTC


def utcnow() -> datetime:
    return datetime.now(UTC)


def format_duration(seconds: float | None) -> str:
    """Định dạng thời gian tồn theo mẫu báo cáo.

    >= 24h  -> "8d 9h"  (giờ lẻ làm tròn xuống)
    <  24h  -> "23.0h", "12.7h" (1 chữ số thập phân)
    """
    if seconds is None:
        return "--"
    seconds = max(0.0, float(seconds))
    hours = seconds / 3600
    if hours >= 24:
        total_h = int(seconds // 3600)
        return f"{total_h // 24}d {total_h % 24}h"
    # Làm tròn xuống để 23.96h không hiển thị thành "24.0h"
    return f"{int(hours * 10) / 10:.1f}h"


def duration_seconds(starts_at: datetime, end: datetime | None, now: datetime) -> float:
    ref = end if end is not None else now
    return max(0.0, (ref - starts_at).total_seconds())


@dataclass(frozen=True)
class ShiftWindow:
    shift: ShiftConfig
    report_date: date  # ngày bắt đầu ca
    start: datetime  # tz-aware (UTC)
    end: datetime  # tz-aware (UTC)

    @property
    def name(self) -> str:
        return self.shift.name

    def is_finished(self, now: datetime) -> bool:
        return now >= self.end

    def contains(self, t: datetime) -> bool:
        return self.start <= t < self.end


def shift_window(shift: ShiftConfig, report_date: date, tz: ZoneInfo) -> ShiftWindow:
    """Khoảng [start, end) của ca. Ca qua đêm: end rơi vào ngày D+1.

    Dùng giờ địa phương rồi chuyển sang UTC (xử lý đúng múi giờ có DST).
    """
    start_local = datetime.combine(report_date, shift.start, tzinfo=tz)
    end_day = report_date + timedelta(days=1) if shift.overnight else report_date
    end_local = datetime.combine(end_day, shift.end, tzinfo=tz)
    return ShiftWindow(shift, report_date, start_local.astimezone(UTC), end_local.astimezone(UTC))


def current_shift(shifts: list[ShiftConfig], now: datetime, tz: ZoneInfo) -> ShiftWindow | None:
    """Ca đang chạy tại thời điểm now (nếu các ca không phủ kín 24h có thể trả None)."""
    local_day = now.astimezone(tz).date()
    for d in (local_day, local_day - timedelta(days=1)):
        for s in shifts:
            w = shift_window(s, d, tz)
            if w.contains(now):
                return w
    return None


def finished_shifts_between(
    shifts: list[ShiftConfig], since: datetime, now: datetime, tz: ZoneInfo
) -> list[ShiftWindow]:
    """Các ca có thời điểm kết thúc trong (since, now], sắp theo thời gian."""
    out: list[ShiftWindow] = []
    d = since.astimezone(tz).date() - timedelta(days=1)
    last = now.astimezone(tz).date()
    while d <= last:
        for s in shifts:
            w = shift_window(s, d, tz)
            if since < w.end <= now:
                out.append(w)
        d += timedelta(days=1)
    return sorted(out, key=lambda w: w.end)


def local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def parse_am_time(value: str | None) -> datetime | None:
    """Parse thời gian RFC3339 của Alertmanager. '0001-01-01T00:00:00Z' => None."""
    if not value or value.startswith("0001-01-01"):
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    # Python < 3.11 không nhận > 6 chữ số phần lẻ giây; cắt bớt cho chắc chắn
    if "." in v:
        head, rest = v.split(".", 1)
        frac = ""
        i = 0
        while i < len(rest) and rest[i].isdigit():
            frac += rest[i]
            i += 1
        v = f"{head}.{frac[:6]}{rest[i:]}"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
