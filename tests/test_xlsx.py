import io
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from trucca.export_xlsx import DATETIME_FORMAT, HEADERS, build_alerts_xlsx, needs_quote_prefix

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
T0 = datetime(2026, 11, 12, 1, 2, 3, tzinfo=UTC)  # = 12/11/2026 08:02:03 giờ VN


def row(i, **kw):
    base = {"stt": i, "alertname": "A", "summary": "s", "duration": "8d 9h", "duration_hours": 201.5,
            "owner": "(chưa gán)", "application": "APP", "count": 1, "starts_at_dt": T0,
            "starts_at": T0.isoformat(), "severity_label": "Warning"}
    base.update(kw)
    return base


def load(rows, **kw):
    return load_workbook(io.BytesIO(build_alerts_xlsx(rows, datetime(2026, 11, 12, tzinfo=UTC), **kw))).active


def test_column_order_start_time_before_duration():
    ws = load([row(1)])
    headers = [c.value for c in ws[1]]
    assert headers == HEADERS
    # Đúng thứ tự yêu cầu; cột mới đứng ngay trước "Thời gian tồn"
    assert headers[:7] == ["STT", "Tên cảnh báo", "Summary", "Thời gian bắt đầu", "Thời gian tồn",
                           "Người xử lý", "Hệ thống"]
    assert headers.index("Thời gian bắt đầu") + 1 == headers.index("Thời gian tồn")


def test_start_time_is_real_datetime_in_config_timezone():
    ws = load([row(1)], tz=TZ)
    c = ws["D2"]
    assert c.is_date and isinstance(c.value, datetime)
    assert c.value == datetime(2026, 11, 12, 8, 2, 3)  # giờ địa phương, không còn tzinfo
    assert c.number_format == DATETIME_FORMAT == "dd/mm/yyyy hh:mm:ss"
    # timezone khác trong config -> giá trị khác
    assert load([row(1)], tz=ZoneInfo("UTC"))["D2"].value == datetime(2026, 11, 12, 1, 2, 3)


def test_rows_keep_given_order_and_stt():
    # Excel xuất đúng thứ tự đang hiển thị trên web (mới nhất trước), STT theo thứ tự đó
    rows = [row(i + 1, alertname=f"A{i}", starts_at_dt=T0 - timedelta(hours=i)) for i in range(3)]
    ws = load(rows, tz=TZ)
    assert [ws.cell(r, 2).value for r in range(2, 5)] == ["A0", "A1", "A2"]
    assert [ws.cell(r, 1).value for r in range(2, 5)] == [1, 2, 3]
    starts = [ws.cell(r, 4).value for r in range(2, 5)]
    assert starts == sorted(starts, reverse=True)


def test_xlsx_structure_and_injection():
    rows = [row(1, summary="=HYPERLINK(\"http://evil\")"), row(2, alertname="+cmd", count=3), row(3, owner="@x")]
    ws = load(rows)
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref == "A1:I4"
    # Lưu dạng chuỗi (không phải công thức) và có quote prefix
    c = ws["C2"]
    assert c.data_type == "s" and c.value.startswith("=") and c.quotePrefix
    assert ws["B3"].value == "+cmd ×3" and ws["B3"].quotePrefix
    assert ws["F4"].quotePrefix  # Người xử lý
    assert ws["E2"].value == "8d 9h" and ws["I2"].value == 201.5
    assert ws["H2"].value == "Warning"
    assert ws["C2"].alignment.wrap_text


def test_missing_start_time_is_blank_not_error():
    ws = load([row(1, starts_at_dt=None, starts_at=None)])
    assert ws["D2"].value is None


def test_needs_quote_prefix():
    assert all(needs_quote_prefix(v) for v in ["=1", "+1", "-1", "@a", "\tx"])
    assert not needs_quote_prefix("abc")


def test_large_export_is_fast():
    import time
    rows = [row(i, summary="x" * 120) for i in range(1, 5001)]
    t0 = time.monotonic()
    build_alerts_xlsx(rows, datetime.now(UTC))
    assert time.monotonic() - t0 < 5
