import io
from datetime import UTC, datetime

from openpyxl import load_workbook

from trucca.export_xlsx import HEADERS, build_alerts_xlsx, needs_quote_prefix


def row(i, **kw):
    base = {"stt": i, "alertname": "A", "summary": "s", "duration": "8d 9h", "duration_hours": 201.5,
            "owner": "(chưa gán)", "application": "APP", "count": 1}
    base.update(kw)
    return base


def test_xlsx_structure_and_injection():
    rows = [row(1, summary="=HYPERLINK(\"http://evil\")"), row(2, alertname="+cmd", count=3), row(3, owner="@x")]
    data = build_alerts_xlsx(rows, datetime(2026, 11, 12, tzinfo=UTC))
    wb = load_workbook(io.BytesIO(data))
    ws = wb.active
    assert [c.value for c in ws[1]] == HEADERS
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref == "A1:G4"
    # Lưu dạng chuỗi (không phải công thức) và có quote prefix
    c = ws["C2"]
    assert c.data_type == "s" and c.value.startswith("=") and c.quotePrefix
    assert ws["B3"].value == "+cmd ×3" and ws["B3"].quotePrefix
    assert ws["E4"].quotePrefix
    assert ws["D2"].value == "8d 9h" and ws["G2"].value == 201.5
    assert ws["C2"].alignment.wrap_text


def test_needs_quote_prefix():
    assert all(needs_quote_prefix(v) for v in ["=1", "+1", "-1", "@a", "\tx"])
    assert not needs_quote_prefix("abc")


def test_large_export_is_fast():
    import time
    rows = [row(i, summary="x" * 120) for i in range(1, 5001)]
    t0 = time.monotonic()
    build_alerts_xlsx(rows, datetime.now(UTC))
    assert time.monotonic() - t0 < 5
