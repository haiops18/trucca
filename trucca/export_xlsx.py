"""Xuất Excel danh sách cảnh báo đang diễn ra."""

from __future__ import annotations

import io
from datetime import datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import xlsxwriter

# Thứ tự cột giống bảng trên web; "Mức độ" (nhóm chuẩn hoá) và "Số giờ tồn" (số, để lọc/sắp xếp) ở cuối.
HEADERS = ["STT", "Tên cảnh báo", "Summary", "Thời gian bắt đầu", "Thời gian tồn", "Người xử lý", "Hệ thống",
           "Mức độ", "Số giờ tồn"]
COL_START = HEADERS.index("Thời gian bắt đầu")
WIDTHS_MIN = [6, 18, 30, 20, 14, 14, 14, 10, 11]
WIDTHS_MAX = [8, 50, 80, 20, 16, 40, 40, 12, 12]
DATETIME_FORMAT = "dd/mm/yyyy hh:mm:ss"
_DEFAULT_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
_INJECTION_CHARS = ("=", "+", "-", "@", "\t", "\r")


def needs_quote_prefix(value: str) -> bool:
    """Chuỗi bắt đầu bằng = + - @ (hoặc tab/CR) có thể bị hiểu là công thức khi mở/chuyển đổi."""
    return value.startswith(_INJECTION_CHARS)


def sanitize_cell(value: str) -> str:
    # Bỏ ký tự điều khiển không hợp lệ trong XML (trừ \t \n)
    return "".join(ch for ch in value if ch in "\t\n" or ord(ch) >= 32)


def _local_naive(value: Any, tz: tzinfo) -> datetime | None:
    """Excel không lưu timezone: đổi sang giờ địa phương theo config rồi bỏ tzinfo."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(tz).replace(tzinfo=None)
    return value


def build_alerts_xlsx(rows: list[dict[str, Any]], generated_at: datetime, title: str = "",
                      tz: tzinfo | None = None) -> bytes:
    """Xuất đúng thứ tự `rows` (đã lọc + sắp xếp như trên web)."""
    tz = tz or _DEFAULT_TZ
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False})
    ws = wb.add_worksheet("Canh bao dang dien ra")

    hdr = wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "#FFFFFF", "border": 1,
                         "valign": "vcenter", "align": "center", "text_wrap": True})
    cell = wb.add_format({"valign": "top", "border": 1, "border_color": "#D9D9D9"})
    cell_q = wb.add_format({"valign": "top", "border": 1, "border_color": "#D9D9D9", "quote_prefix": True})
    wrap = wb.add_format({"valign": "top", "border": 1, "border_color": "#D9D9D9", "text_wrap": True})
    wrap_q = wb.add_format({"valign": "top", "border": 1, "border_color": "#D9D9D9", "text_wrap": True,
                            "quote_prefix": True})
    num = wb.add_format({"valign": "top", "border": 1, "border_color": "#D9D9D9", "num_format": "0.00"})
    center = wb.add_format({"valign": "top", "border": 1, "border_color": "#D9D9D9", "align": "center"})
    dt_fmt = wb.add_format({"valign": "top", "border": 1, "border_color": "#D9D9D9", "align": "center",
                            "num_format": DATETIME_FORMAT})

    # Tính độ rộng cột trước (XlsxWriter không tự auto-fit)
    widths = list(WIDTHS_MIN)
    for r in rows:
        values = [str(r["stt"]), r["alertname"], r["summary"], "", r["duration"], r["owner"], r["application"],
                  r.get("severity_label", ""), ""]
        for i, v in enumerate(values):
            longest = max((len(line) for line in str(v).split("\n")), default=0)
            widths[i] = min(WIDTHS_MAX[i], max(widths[i], longest + 2))
    for i, w in enumerate(widths):
        ws.set_column(i, i, w)

    ws.write_row(0, 0, HEADERS, hdr)
    ws.set_row(0, 22)
    for idx, r in enumerate(rows, start=1):
        ws.write_number(idx, 0, r["stt"], center)
        start = _local_naive(r.get("starts_at_dt") or r.get("starts_at"), tz)
        if start is not None:
            ws.write_datetime(idx, COL_START, start, dt_fmt)
        else:
            ws.write_blank(idx, COL_START, None, cell)
        for col, key in ((1, "alertname"), (2, "summary"), (4, "duration"), (5, "owner"), (6, "application"),
                         (7, "severity_label")):
            value = sanitize_cell(str(r.get(key) or ""))
            if key == "alertname" and r.get("count", 1) > 1:
                value = f"{value} ×{r['count']}"
            is_wrap = key == "summary"
            fmt = (wrap_q if is_wrap else cell_q) if needs_quote_prefix(value) else (wrap if is_wrap else cell)
            ws.write_string(idx, col, value, fmt)
        ws.write_number(idx, 8, r["duration_hours"], num)

    last_row = max(len(rows), 1)
    ws.autofilter(0, 0, last_row, len(HEADERS) - 1)
    ws.freeze_panes(1, 0)
    wb.set_properties({"title": title or "Cảnh báo đang diễn ra", "author": "TrucCa",
                       "comments": f"Xuất lúc {generated_at.isoformat()}"})
    wb.close()
    return buf.getvalue()
