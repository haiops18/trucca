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


# Chế độ tra cứu theo thời điểm: thêm trạng thái hiện tại + thời điểm clear ngay sau "Hệ thống"
HISTORY_HEADERS = [*HEADERS[:7], "Trạng thái", "Thời điểm clear", *HEADERS[7:]]

# (tiêu đề, khoá dữ liệu, kiểu, độ rộng min, độ rộng max)
_COLUMNS: dict[str, tuple[str, str, int, int]] = {
    "STT": ("stt", "stt", 6, 8),
    "Tên cảnh báo": ("alertname", "text", 18, 50),
    "Summary": ("summary", "wrap", 30, 80),
    "Thời gian bắt đầu": ("starts_at", "datetime", 20, 20),
    "Thời gian tồn": ("duration", "text", 14, 16),
    "Người xử lý": ("owner", "text", 14, 40),
    "Hệ thống": ("application", "text", 14, 40),
    "Trạng thái": ("status_label", "text", 13, 16),
    "Thời điểm clear": ("resolved_at", "datetime", 20, 20),
    "Mức độ": ("severity_label", "text", 10, 12),
    "Số giờ tồn": ("duration_hours", "number", 11, 12),
}


def build_alerts_xlsx(rows: list[dict[str, Any]], generated_at: datetime, title: str = "",
                      tz: tzinfo | None = None, at: datetime | None = None) -> bytes:
    """Xuất đúng thứ tự `rows` (đã lọc + sắp xếp như trên web).

    `at` khác None: chế độ tra cứu cảnh báo tại thời điểm đó (thêm cột Trạng thái, Thời điểm clear;
    "Thời gian tồn" tính tới `at`)."""
    tz = tz or _DEFAULT_TZ
    headers = HISTORY_HEADERS if at is not None else HEADERS
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False})
    sheet = f"Tai thoi diem {at.astimezone(tz):%d-%m-%Y %Hh%M}" if at is not None else "Canh bao dang dien ra"
    ws = wb.add_worksheet(sheet)

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

    specs = [_COLUMNS[h] for h in headers]

    def text_of(r: dict[str, Any], key: str) -> str:
        value = sanitize_cell(str(r.get(key) or ""))
        if key == "alertname" and r.get("count", 1) > 1:
            value = f"{value} ×{r['count']}"
        return value

    # Tính độ rộng cột trước (XlsxWriter không tự auto-fit)
    widths = [lo for _, _, lo, _ in specs]
    for r in rows:
        for i, (key, kind, _lo, hi) in enumerate(specs):
            if kind in ("text", "wrap"):
                longest = max((len(line) for line in text_of(r, key).split("\n")), default=0)
                widths[i] = min(hi, max(widths[i], longest + 2))
    for i, w in enumerate(widths):
        ws.set_column(i, i, w)

    ws.write_row(0, 0, headers, hdr)
    ws.set_row(0, 22)
    for idx, r in enumerate(rows, start=1):
        for col, (key, kind, _lo, _hi) in enumerate(specs):
            if kind == "stt":
                ws.write_number(idx, col, r["stt"], center)
            elif kind == "number":
                ws.write_number(idx, col, r["duration_hours"], num)
            elif kind == "datetime":
                value = _local_naive(r.get(f"{key}_dt") or r.get(key), tz)
                if value is not None:
                    ws.write_datetime(idx, col, value, dt_fmt)
                else:
                    ws.write_blank(idx, col, None, cell)
            else:
                value = text_of(r, key)
                is_wrap = kind == "wrap"
                fmt = (wrap_q if is_wrap else cell_q) if needs_quote_prefix(value) else (wrap if is_wrap else cell)
                ws.write_string(idx, col, value, fmt)

    last_row = max(len(rows), 1)
    ws.autofilter(0, 0, last_row, len(headers) - 1)
    ws.freeze_panes(1, 0)
    default_title = (f"Cảnh báo tại thời điểm {at.astimezone(tz):%d/%m/%Y %H:%M:%S}" if at is not None
                     else "Cảnh báo đang diễn ra")
    wb.set_properties({"title": title or default_title, "author": "TrucCa",
                       "comments": f"Xuất lúc {generated_at.isoformat()}"})
    wb.close()
    return buf.getvalue()
