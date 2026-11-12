"""Truy vấn cảnh báo đang diễn ra (dùng chung cho giao diện và xuất Excel)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, literal, or_, select
from sqlalchemy.engine import Engine

from .db import alerts
from .severity import LABELS as SEVERITY_LABELS
from .severity import LEVELS as SEVERITY_LEVELS
from .severity import RANK as SEVERITY_RANK
from .severity import clean, parse_level
from .timeutil import duration_seconds, format_duration

SortKey = Literal["starts_at", "duration", "alertname", "summary", "owner", "application", "severity"]
SORT_KEYS: tuple[str, ...] = ("starts_at", "duration", "alertname", "summary", "owner", "application", "severity")
# Mặc định: cảnh báo bắt đầu MỚI NHẤT ở trên cùng
DEFAULT_SORT, DEFAULT_ORDER = "starts_at", "desc"
DISPLAY_TIME_FORMAT = "%d/%m/%Y %H:%M:%S"
_DEFAULT_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@dataclass
class AlertFilter:
    q: str = ""
    application: str = ""
    owner: str = ""
    alertname: str = ""
    severity: str = ""
    sort: str = DEFAULT_SORT
    order: str = DEFAULT_ORDER

    def normalized(self) -> AlertFilter:
        sort = self.sort if self.sort in SORT_KEYS else DEFAULT_SORT
        order = "asc" if self.order == "asc" else "desc"
        return AlertFilter(
            self.q.strip()[:200], self.application.strip(), self.owner.strip(),
            self.alertname.strip(), self.severity.strip(), sort, order,
        )


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Giá trị đặc biệt trong dropdown để lọc các dòng thiếu label
MISSING = "__missing__"


def fetch_active(engine: Engine, f: AlertFilter) -> list[dict[str, Any]]:
    f = f.normalized()
    stmt = select(
        alerts.c.fingerprint, alerts.c.alertname, alerts.c.summary, alerts.c.severity,
        alerts.c.severity_normalized, alerts.c.owner, alerts.c.application, alerts.c.instance,
        alerts.c.starts_at, alerts.c.am_state,
    ).where(alerts.c.status == "active")
    for col, val in (
        (alerts.c.application, f.application),
        (alerts.c.owner, f.owner),
        (alerts.c.alertname, f.alertname),
    ):
        if val == MISSING:
            stmt = stmt.where(col.is_(None))
        elif val:
            stmt = stmt.where(col == val)
    if f.severity:
        level = parse_level(f.severity)
        if level:
            # severity=warning khớp Warning / WARNING / warning ... (đã chuẩn hoá khi thu thập)
            stmt = stmt.where(alerts.c.severity_normalized == level)
        elif f.severity == MISSING:
            stmt = stmt.where(alerts.c.severity.is_(None))
        else:
            # Tương thích ngược: lọc theo giá trị gốc, không phân biệt hoa thường
            stmt = stmt.where(func.lower(func.btrim(alerts.c.severity)) == clean(f.severity))
    if f.q:
        pat = f"%{_escape_like(f.q)}%"
        stmt = stmt.where(
            or_(
                alerts.c.alertname.ilike(pat, escape="\\"),
                alerts.c.summary.ilike(pat, escape="\\"),
                alerts.c.owner.ilike(pat, escape="\\"),
                alerts.c.application.ilike(pat, escape="\\"),
                alerts.c.instance.ilike(pat, escape="\\"),
            )
        )
    # Sắp xếp phụ cố định để thứ tự ổn định giữa các lần refresh
    if f.sort == "duration":
        # thời gian tồn giảm dần <=> starts_at tăng dần
        primary = alerts.c.starts_at.asc() if f.order == "desc" else alerts.c.starts_at.desc()
    elif f.sort == "starts_at":
        primary = alerts.c.starts_at.desc() if f.order == "desc" else alerts.c.starts_at.asc()
    elif f.sort == "severity":
        rank = case(*[(alerts.c.severity_normalized == lv, literal(r)) for lv, r in SEVERITY_RANK.items()],
                    else_=literal(0))
        primary = rank.desc() if f.order == "desc" else rank.asc()
    else:
        col = alerts.c[f.sort]
        primary = (col.asc() if f.order == "asc" else col.desc()).nulls_last()
    stmt = stmt.order_by(primary, alerts.c.starts_at.asc(), alerts.c.fingerprint)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(stmt)]


def format_start(ts: datetime, tz: tzinfo | None) -> str:
    """Thời gian bắt đầu hiển thị: dd/MM/yyyy HH:mm:ss theo timezone cấu hình."""
    return ts.astimezone(tz or _DEFAULT_TZ).strftime(DISPLAY_TIME_FORMAT)


def _severity_fields(level: str, raw: str) -> dict[str, str]:
    level = level if level in SEVERITY_LEVELS else "other"
    return {"severity": raw, "severity_level": level, "severity_label": SEVERITY_LABELS[level]}


def present_rows(rows: list[dict[str, Any]], now: datetime, missing_text: str,
                 tz: tzinfo | None = None) -> list[dict[str, Any]]:
    """STT đánh theo đúng thứ tự hiển thị (sau lọc + sắp xếp)."""
    out = []
    for i, r in enumerate(rows, 1):
        secs = duration_seconds(r["starts_at"], None, now)
        out.append(
            {
                "stt": i,
                "fingerprint": r["fingerprint"],
                "alertname": r["alertname"],
                "summary": r["summary"] or "",
                "starts_at_display": format_start(r["starts_at"], tz),
                "duration": format_duration(secs),
                "duration_hours": round(secs / 3600, 2),
                "owner": r["owner"] or missing_text,
                "application": r["application"] or missing_text,
                **_severity_fields(r.get("severity_normalized") or "other", r["severity"] or ""),
                "instance": r["instance"] or "",
                "starts_at": r["starts_at"].isoformat(),
                "starts_at_dt": r["starts_at"],
                "silenced": r["am_state"] == "suppressed",
                "count": 1,
            }
        )
    return out


def group_summary(summaries: list[str], instances: list[str], count: int) -> str:
    """Summary hiển thị cho nhóm gộp: '(3) 10.0.0.1:9100 – 10.0.0.3:9100' như mẫu."""
    inst = sorted({i for i in instances if i})
    if count > 1 and inst:
        return f"({count}) {inst[0]} – {inst[-1]}" if len(inst) > 1 else f"({count}) {inst[0]}"
    uniq = sorted({s for s in summaries if s})
    if len(uniq) == 1:
        return uniq[0] if count == 1 else f"({count}) {uniq[0]}"
    return f"({count}) {uniq[0]} …" if uniq else ""


def group_rows(rows: list[dict[str, Any]], now: datetime, missing_text: str, tz: tzinfo | None = None,
               sort: str = DEFAULT_SORT, order: str = DEFAULT_ORDER) -> list[dict[str, Any]]:
    """Gộp các cảnh báo cùng alertname + application.

    Thời gian bắt đầu của nhóm = cảnh báo bắt đầu sớm nhất (khớp với "Thời gian tồn" của nhóm). Khi sắp xếp theo
    thời gian bắt đầu / thời gian tồn, các nhóm được sắp lại theo giá trị này; các cách sắp xếp khác giữ thứ tự
    dòng đầu tiên của mỗi nhóm. STT đánh lại theo thứ tự hiển thị cuối cùng.
    """
    groups: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r["alertname"], r["application"]), []).append(r)
    out = []
    for (alertname, app), members in groups.items():
        oldest = min(m["starts_at"] for m in members)
        secs = duration_seconds(oldest, None, now)
        owners = sorted({m["owner"] for m in members if m["owner"]})
        sev = sorted({m["severity"] for m in members if m["severity"]})
        level = max((m.get("severity_normalized") or "other" for m in members),
                    key=lambda lv: SEVERITY_RANK.get(lv, 0))
        out.append(
            {
                "stt": 0,
                "fingerprint": members[0]["fingerprint"],
                "alertname": alertname,
                "summary": group_summary([m["summary"] for m in members], [m["instance"] or "" for m in members],
                                         len(members)),
                "starts_at_display": format_start(oldest, tz),
                "duration": format_duration(secs),
                "duration_hours": round(secs / 3600, 2),
                "owner": ", ".join(owners) if owners else missing_text,
                "application": app or missing_text,
                **_severity_fields(level, ", ".join(sev)),
                "instance": "",
                "starts_at": oldest.isoformat(),
                "starts_at_dt": oldest,
                "silenced": all(m["am_state"] == "suppressed" for m in members),
                "count": len(members),
            }
        )
    if sort in ("starts_at", "duration"):
        newest_first = (sort == "starts_at") == (order == "desc")
        out.sort(key=lambda g: g["starts_at_dt"], reverse=newest_first)
    for i, g in enumerate(out, 1):
        g["stt"] = i
    return out


def severity_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Số cảnh báo theo nhóm mức độ (trên dòng thô, trước khi gộp)."""
    counts = dict.fromkeys(SEVERITY_LEVELS, 0)
    for r in rows:
        lv = r.get("severity_normalized") or "other"
        counts[lv if lv in counts else "other"] += 1
    return counts


def strip_internal(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bỏ các trường nội bộ (datetime) trước khi trả JSON."""
    return [{k: v for k, v in r.items() if k != "starts_at_dt"} for r in rows]


def age_edges(handover_hours: float) -> list[float]:
    """Mốc phân nhóm tuổi cảnh báo (giờ). Ngưỡng bàn giao luôn là một mốc."""
    return sorted({1.0, float(handover_hours), 24.0, 168.0} - {0.0})


def _fmt_hours(h: float) -> str:
    if h >= 24 and h % 24 == 0:
        return f"{int(h // 24)} ngày"
    return f"{h:g}h"


def age_buckets(rows: list[dict[str, Any]], now: datetime, handover_hours: float) -> list[dict[str, Any]]:
    """Đếm cảnh báo theo nhóm tuổi; 'level' dùng để tô màu (0 = mới ... 4 = rất lâu)."""
    edges = age_edges(handover_hours)
    bounds = [0.0, *edges, None]
    buckets = []
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        if lo == 0:
            label = f"Dưới {_fmt_hours(hi)}"
        elif hi is None:
            label = f"Trên {_fmt_hours(lo)}"
        else:
            label = f"{_fmt_hours(lo)} – {_fmt_hours(hi)}"
        buckets.append({"min_hours": lo, "max_hours": hi, "label": label, "count": 0,
                        "level": min(4, round(i * 4 / max(1, len(bounds) - 2))),
                        "over_handover": lo >= handover_hours})
    for r in rows:
        h = duration_seconds(r["starts_at"], None, now) / 3600
        for b in buckets:
            if h >= b["min_hours"] and (b["max_hours"] is None or h < b["max_hours"]):
                b["count"] += 1
                break
    return buckets


def filter_age(rows: list[dict[str, Any]], now: datetime, min_hours: float | None,
               max_hours: float | None) -> list[dict[str, Any]]:
    if min_hours is None and max_hours is None:
        return rows
    out = []
    for r in rows:
        h = duration_seconds(r["starts_at"], None, now) / 3600
        if (min_hours is None or h >= min_hours) and (max_hours is None or h < max_hours):
            out.append(r)
    return out


def filter_options(engine: Engine) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    with engine.connect() as conn:
        present = {r[0] for r in conn.execute(
            select(alerts.c.severity_normalized).where(alerts.c.status == "active").distinct())}
        # Mức độ: luôn là các nhóm chuẩn (critical, warning, other) theo thứ tự cố định
        out["severity"] = [lv for lv in SEVERITY_LEVELS if lv in present]
        for name in ("application", "owner", "alertname"):
            col = alerts.c[name]
            stmt = (
                select(col, func.count())
                .where(alerts.c.status == "active")
                .group_by(col)
                .order_by(col.asc().nulls_last())
            )
            out[name] = [r[0] if r[0] is not None else MISSING for r in conn.execute(stmt)]
    return out
