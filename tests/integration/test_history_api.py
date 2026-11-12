"""API tra cứu cảnh báo tại một thời điểm (bảng alert_occurrences, PostgreSQL thật)."""

import io
from datetime import UTC, datetime, timedelta

import pytest
from openpyxl import load_workbook
from sqlalchemy import delete, insert

T = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)  # thời điểm tra cứu: hôm qua


@pytest.fixture()
def history(engine):
    from trucca.db import occurrences

    def occ(fp, start_h, end_h=None, sev="Warning", level="warning", app="HIS", source=None):
        start = T - timedelta(hours=start_h)
        return dict(fingerprint=fp, alertname=f"Alert {fp}", summary=f"sum {fp}", severity=sev,
                    severity_normalized=level, owner="noc", application=app, instance=None,
                    labels={"alertname": f"Alert {fp}"}, starts_at=start, first_seen_at=start,
                    resolved_at=None if end_h is None else T + timedelta(hours=end_h), resolve_source=source)

    rows = [
        occ("h-active", 5),                                  # bắt đầu trước T, chưa clear -> có, Đang diễn ra
        occ("h-cleared-later", 3, end_h=2, sev="CRITICAL", level="critical"),  # clear sau T -> có, Đã clear
        occ("h-cleared-before", 10, end_h=-1),               # clear trước T -> không
        occ("h-starts-after", -1),                           # bắt đầu sau T -> không
        occ("h-filtered", 4, end_h=1, source="filtered_out"),  # bị loại do filter cấu hình -> không
        occ("h-exact", 0, end_h=0.5, app="ERP"),             # bắt đầu đúng T -> có
    ]
    with engine.begin() as c:
        c.execute(delete(occurrences).where(occurrences.c.fingerprint.like("h-%")))
        c.execute(insert(occurrences), rows)
    yield
    with engine.begin() as c:
        c.execute(delete(occurrences).where(occurrences.c.fingerprint.like("h-%")))


def _at(dt):
    return dt.isoformat()


def _get(sess, **params):
    r = sess.get("/api/alerts", params={"page_size": 1000, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_point_in_time_lookup(viewer, history):
    data = _get(viewer, at=_at(T))
    assert data["mode"] == "history"
    items = {i["fingerprint"]: i for i in data["items"] if i["fingerprint"].startswith("h-")}
    assert set(items) == {"h-active", "h-cleared-later", "h-exact"}
    assert items["h-active"]["status"] == "active" and items["h-active"]["status_label"] == "Đang diễn ra"
    assert items["h-active"]["duration"] == "5.0h"  # tồn tại thời điểm tra cứu
    assert items["h-cleared-later"]["status"] == "resolved"
    assert items["h-cleared-later"]["resolved_at_display"]
    assert data["at_display"] and "data_since" in data and data["retention_days"] == 90
    # Mặc định sắp theo thời gian bắt đầu giảm dần, STT liên tục
    starts = [datetime.fromisoformat(i["starts_at"]) for i in data["items"]]
    assert starts == sorted(starts, reverse=True)
    assert [i["stt"] for i in data["items"]] == list(range(1, len(data["items"]) + 1))


def test_naive_local_time_param(viewer, history):
    local = T.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Ho_Chi_Minh")).replace(tzinfo=None)
    data = _get(viewer, at=local.strftime("%Y-%m-%dT%H:%M:%S"))
    assert {i["fingerprint"] for i in data["items"] if i["fingerprint"].startswith("h-")} == \
        {"h-active", "h-cleared-later", "h-exact"}


def test_filters_apply_in_history(viewer, history):
    data = _get(viewer, at=_at(T), severity="critical")
    assert [i["fingerprint"] for i in data["items"] if i["fingerprint"].startswith("h-")] == ["h-cleared-later"]
    assert data["severity_counts"]["critical"] >= 1
    assert {i["fingerprint"] for i in _get(viewer, at=_at(T), application="ERP")["items"]} == {"h-exact"}
    opts = viewer.get("/api/alerts/filters", params={"at": _at(T)}).json()
    assert "ERP" in opts["application"] and "HIS" in opts["application"]


def test_live_mode_unchanged(viewer, history):
    data = _get(viewer)
    assert data["mode"] == "live" and "at" not in data
    assert not any(i["fingerprint"].startswith("h-") for i in data["items"])  # lịch sử không lẫn vào đang diễn ra
    assert all("status" not in i for i in data["items"])


def test_history_excel(viewer, history):
    r = viewer.get("/api/alerts/export.xlsx", params={"at": _at(T)})
    assert r.status_code == 200
    assert "canh-bao-tai-thoi-diem_" in r.headers["content-disposition"]
    ws = load_workbook(io.BytesIO(r.content)).active
    hdr = [c.value for c in ws[1]]
    assert hdr[7:9] == ["Trạng thái", "Thời điểm clear"]
    rows = {row[1]: row for row in ws.iter_rows(min_row=2, values_only=True)}
    assert rows["Alert h-active"][7] == "Đang diễn ra" and rows["Alert h-active"][8] is None
    assert rows["Alert h-cleared-later"][7] == "Đã clear" and isinstance(rows["Alert h-cleared-later"][8], datetime)


@pytest.mark.parametrize("value,code", [("2099-01-01T00:00", "future_at"), ("hôm qua", "invalid_at")])
def test_bad_at(viewer, value, code):
    r = viewer.get("/api/alerts", params={"at": value})
    assert r.status_code == 400 and r.json()["error"]["code"] == code
    assert viewer.get("/api/alerts/export.xlsx", params={"at": value}).status_code == 400


def test_history_requires_login(app_client):
    from fastapi.testclient import TestClient

    anon = TestClient(app_client[0], follow_redirects=False)
    assert anon.get("/api/alerts", params={"at": _at(T)}).status_code == 401
