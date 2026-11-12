from datetime import date

from tests.conftest import TZ, vn
from trucca.reports.calc import Occ, build_report
from trucca.reports.render import ReportRenderer, trend_svg
from trucca.timeutil import shift_window


def test_render_escapes_and_handles_empty(shifts):
    w = shift_window(shifts[1], date(2026, 11, 11), TZ)
    y = shift_window(shifts[1], date(2026, 11, 10), TZ)
    occs = [Occ(vn(2026, 11, 11, 19), None, "<script>alert(1)</script>", "<b>APP</b>", "x")]
    r = build_report(occs=occs, window=w, yesterday=y, now=vn(2026, 11, 11, 20), tz=TZ, title="Báo cáo cuối ca",
                     top_count=5, trend_days=7, handover_threshold_hours=10, handover_limit=15, missing_text="-")
    html = ReportRenderer().render_html(r)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "CA_2 • 18:00 - 08:00" in html and "Chỉ chốt khi ca trực kết thúc" in html
    assert "Không có cảnh báo tồn quá ngưỡng" in html


def test_trend_svg_zero_values():
    svg = str(trend_svg([{"label": "01/11", "count": 0}, {"label": "02/11", "count": 0}]))
    assert svg.startswith("<svg") and "01/11" in svg
