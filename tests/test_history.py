"""Tra cứu cảnh báo tại một thời điểm: tham số `at`, trạng thái, Excel."""

import io
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from openpyxl import load_workbook

from trucca.alerts_query import group_rows, present_rows
from trucca.export_xlsx import HEADERS, HISTORY_HEADERS, build_alerts_xlsx
from trucca.web.api import parse_at

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


def test_parse_at_empty_is_live():
    assert parse_at("", TZ, NOW) is None
    assert parse_at("   ", TZ, NOW) is None


def test_parse_at_naive_uses_config_timezone():
    # 12/11/2026 08:30 giờ VN = 01:30 UTC
    assert parse_at("2026-11-12T08:30", TZ, NOW) == datetime(2026, 11, 12, 1, 30, tzinfo=UTC)
    assert parse_at("2026-11-12T08:30:15", TZ, NOW) == datetime(2026, 11, 12, 1, 30, 15, tzinfo=UTC)
    assert parse_at("2026-11-12 08:30", TZ, NOW) == datetime(2026, 11, 12, 1, 30, tzinfo=UTC)


def test_parse_at_explicit_offset():
    assert parse_at("2026-11-12T01:30:00Z", TZ, NOW) == datetime(2026, 11, 12, 1, 30, tzinfo=UTC)
    assert parse_at("2026-11-12T09:30:00+08:00", TZ, NOW) == datetime(2026, 11, 12, 1, 30, tzinfo=UTC)


@pytest.mark.parametrize("raw,code", [("abc", "invalid_at"), ("2026-13-01T00:00", "invalid_at"),
                                      ("2026-11-12T23:00", "future_at")])
def test_parse_at_errors(raw, code):
    with pytest.raises(HTTPException) as e:
        parse_at(raw, TZ, NOW)
    assert e.value.status_code == 400 and e.value.detail["code"] == code


def raw(fp, start_h, resolved_h=None, name="A", app="APP"):
    return {"fingerprint": fp, "alertname": name, "summary": "", "severity": "warning",
            "severity_normalized": "warning", "owner": None, "application": app, "instance": None,
            "starts_at": NOW - timedelta(hours=start_h),
            "resolved_at": None if resolved_h is None else NOW + timedelta(hours=resolved_h), "am_state": None}


def test_history_rows_have_status_and_duration_at_point():
    at = NOW
    rows = present_rows([raw("a", 5), raw("b", 3, resolved_h=2)], at, "-", TZ)
    a, b = rows
    assert (a["status"], a["status_label"], a["resolved_at"]) == ("active", "Đang diễn ra", None)
    assert a["duration"] == "5.0h"  # tính tới thời điểm tra cứu, không phải tới bây giờ
    assert (b["status"], b["status_label"]) == ("resolved", "Đã clear")
    assert b["resolved_at_display"] == "12/11/2026 19:00:00"  # 12:00 UTC
    assert b["total_duration"] == "5.0h"


def test_live_rows_have_no_status():
    r = dict(raw("a", 5))
    r.pop("resolved_at")
    assert "status" not in present_rows([r], NOW, "-", TZ)[0]


def test_group_status():
    rows = [raw("a", 5, resolved_h=1), raw("b", 4, resolved_h=3)]
    g = group_rows(rows, NOW, "-", TZ)[0]
    assert g["status"] == "resolved" and g["resolved_at"] == (NOW + timedelta(hours=3)).isoformat()
    rows.append(raw("c", 2))
    assert group_rows(rows, NOW, "-", TZ)[0]["status"] == "active"


def test_history_excel_columns():
    at = datetime(2026, 11, 12, 3, 0, tzinfo=UTC)
    rows = present_rows([raw("a", 5), raw("b", 3, resolved_h=2)], at, "-", TZ)
    wb = load_workbook(io.BytesIO(build_alerts_xlsx(rows, NOW, tz=TZ, at=at)))
    ws = wb.active
    hdr = [c.value for c in ws[1]]
    assert hdr == HISTORY_HEADERS
    assert hdr[:7] == HEADERS[:7] and hdr[7:9] == ["Trạng thái", "Thời điểm clear"]
    assert ws.title == "Tai thoi diem 12-11-2026 10h00"
    assert ws["H2"].value == "Đang diễn ra" and ws["I2"].value is None
    assert ws["H3"].value == "Đã clear" and ws["I3"].is_date
    assert ws["I3"].value == datetime(2026, 11, 12, 19, 0)  # giờ VN
    # Chế độ thường giữ nguyên cột
    live = load_workbook(io.BytesIO(build_alerts_xlsx(rows, NOW, tz=TZ))).active
    assert [c.value for c in live[1]] == HEADERS
