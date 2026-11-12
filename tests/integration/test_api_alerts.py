"""API cảnh báo: lọc mức độ (không phân biệt hoa thường), sắp xếp mặc định, Excel theo đúng thứ tự web."""

import io
from datetime import datetime

from openpyxl import load_workbook

from .conftest import NOW


def _items(sess, **params):
    r = sess.get("/api/alerts", params={"page_size": 1000, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_default_sort_newest_start_first(viewer, seeded):
    data = _items(viewer)
    assert (data["sort"], data["order"]) == ("starts_at", "desc")
    items = [i for i in data["items"] if i["fingerprint"].startswith("it-")]
    starts = [datetime.fromisoformat(i["starts_at"]) for i in items]
    assert starts == sorted(starts, reverse=True)
    assert items[0]["fingerprint"] == "it-c3"  # bắt đầu 30 phút trước = mới nhất
    assert [i["stt"] for i in data["items"]] == list(range(1, len(data["items"]) + 1))
    # Định dạng hiển thị dd/MM/yyyy HH:mm:ss
    assert len(items[0]["starts_at_display"]) == 19 and items[0]["starts_at_display"][2] == "/"


def test_user_can_change_sort(viewer, seeded):
    asc = [i["fingerprint"] for i in _items(viewer, sort="starts_at", order="asc")["items"]]
    desc = [i["fingerprint"] for i in _items(viewer, sort="starts_at", order="desc")["items"]]
    assert asc == list(reversed(desc))
    # Thời gian tồn giảm dần = bắt đầu sớm nhất trước
    assert _items(viewer, sort="duration", order="desc")["items"][0]["fingerprint"] == "it-c2"


def test_severity_filter_matches_all_case_variants(viewer, seeded):
    for value in ("warning", "Warning", "WARNING", " warning "):
        fps = {i["fingerprint"] for i in _items(viewer, severity=value)["items"]}
        assert fps == {"it-w1", "it-w2", "it-w3", "it-w4"}, value
    assert {i["fingerprint"] for i in _items(viewer, severity="critical")["items"]} == {"it-c1", "it-c2", "it-c3"}
    assert {i["fingerprint"] for i in _items(viewer, severity="other")["items"]} == {"it-o1", "it-o2"}
    # Badge: nhóm chuẩn + giá trị gốc
    w = next(i for i in _items(viewer, severity="warning")["items"] if i["fingerprint"] == "it-w2")
    assert (w["severity_level"], w["severity_label"], w["severity"]) == ("warning", "Warning", "WARNING")


def test_severity_counts_and_filter_options(viewer, seeded):
    data = _items(viewer)
    assert data["severity_counts"] == {"critical": 3, "warning": 4, "other": 2}
    # Đếm theo mức độ không bị chính bộ lọc mức độ làm mất (để còn bấm chuyển nhóm)
    assert _items(viewer, severity="critical")["severity_counts"] == {"critical": 3, "warning": 4, "other": 2}
    assert viewer.get("/api/alerts/filters").json()["severity"] == ["critical", "warning", "other"]


def test_grouped_view_sorted_and_renumbered(viewer, seeded):
    data = _items(viewer, group="true")
    starts = [datetime.fromisoformat(i["starts_at"]) for i in data["items"]]
    assert starts == sorted(starts, reverse=True)
    assert [i["stt"] for i in data["items"]] == list(range(1, len(data["items"]) + 1))


def test_excel_matches_web_order_and_filters(viewer, seeded):
    params = {"severity": "WARNING", "sort": "starts_at", "order": "desc"}
    web = [i["alertname"] for i in _items(viewer, **params)["items"]]
    r = viewer.get("/api/alerts/export.xlsx", params=params)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    ws = load_workbook(io.BytesIO(r.content)).active
    headers = [c.value for c in ws[1]]
    assert headers[:7] == ["STT", "Tên cảnh báo", "Summary", "Thời gian bắt đầu", "Thời gian tồn",
                           "Người xử lý", "Hệ thống"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert [row[1] for row in rows] == web  # đúng thứ tự đang hiển thị
    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))
    starts = [row[3] for row in rows]
    assert all(isinstance(s, datetime) for s in starts) and starts == sorted(starts, reverse=True)
    assert ws.cell(2, 4).number_format == "dd/mm/yyyy hh:mm:ss"
    assert {row[7] for row in rows} == {"Warning"}
    # Giờ địa phương (Asia/Ho_Chi_Minh, UTC+7) của cảnh báo mới nhất trong nhóm warning (1 giờ trước)
    expected = (NOW.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Ho_Chi_Minh"))).replace(tzinfo=None)
    assert abs((expected - starts[0]).total_seconds() - 3600) < 2


def test_unauthenticated_api_rejected(app_client):
    from fastapi.testclient import TestClient

    anon = TestClient(app_client[0], follow_redirects=False)
    assert anon.get("/api/alerts").status_code == 401
    assert anon.get("/api/alerts/export.xlsx").status_code == 401
