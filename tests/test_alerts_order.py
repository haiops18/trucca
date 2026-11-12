from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from trucca.alerts_query import (
    DEFAULT_ORDER,
    DEFAULT_SORT,
    AlertFilter,
    format_start,
    group_rows,
    present_rows,
    severity_counts,
)
from trucca.reports.calc import Occ, handover

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def raw(fp, hours_ago, name="A", app="APP", sev="warning", level="warning", inst=None):
    return {"fingerprint": fp, "alertname": name, "summary": "", "severity": sev, "severity_normalized": level,
            "owner": None, "application": app, "instance": inst, "starts_at": NOW - timedelta(hours=hours_ago),
            "am_state": "active"}


def test_default_sort_is_newest_start_first():
    assert (DEFAULT_SORT, DEFAULT_ORDER) == ("starts_at", "desc")
    f = AlertFilter().normalized()
    assert (f.sort, f.order) == ("starts_at", "desc")
    assert AlertFilter(sort="bogus").normalized().sort == "starts_at"


def test_format_start_uses_config_timezone():
    ts = datetime(2026, 11, 12, 1, 2, 3, tzinfo=UTC)
    assert format_start(ts, TZ) == "12/11/2026 08:02:03"
    assert format_start(ts, ZoneInfo("UTC")) == "12/11/2026 01:02:03"


def test_present_rows_stt_follows_display_order_and_has_start():
    rows = present_rows([raw("a", 1), raw("b", 5), raw("c", 9)], NOW, "(chưa gán)", TZ)
    assert [r["stt"] for r in rows] == [1, 2, 3]
    assert [r["fingerprint"] for r in rows] == ["a", "b", "c"]
    assert rows[0]["starts_at_display"] == format_start(NOW - timedelta(hours=1), TZ)
    assert rows[0]["severity_level"] == "warning" and rows[0]["severity_label"] == "Warning"


def test_group_rows_sorted_by_start_desc_and_renumbered():
    # Dòng thô đã sắp theo starts_at giảm dần (như SQL trả về)
    rows = [raw("n1", 1, name="New"), raw("o1", 2, name="Old"), raw("n2", 3, name="Mid"),
            raw("o2", 50, name="Old")]
    g = group_rows(rows, NOW, "(chưa gán)", TZ, "starts_at", "desc")
    # Nhóm "Old" có cảnh báo sớm nhất 50h trước -> xếp cuối khi mới nhất ở trên
    assert [x["alertname"] for x in g] == ["New", "Mid", "Old"]
    assert [x["stt"] for x in g] == [1, 2, 3]
    assert g[2]["count"] == 2 and g[2]["starts_at_display"] == format_start(NOW - timedelta(hours=50), TZ)
    asc = group_rows(list(reversed(rows)), NOW, "(chưa gán)", TZ, "starts_at", "asc")
    assert [x["alertname"] for x in asc] == ["Old", "Mid", "New"]


def test_group_severity_is_most_severe_member():
    rows = [raw("a", 1, sev="Warning", level="warning"), raw("b", 2, sev="CRITICAL", level="critical")]
    g = group_rows(rows, NOW, "-", TZ)
    assert g[0]["severity_level"] == "critical" and g[0]["severity"] == "CRITICAL, Warning"


def test_severity_counts():
    rows = [raw("a", 1, level="critical"), raw("b", 1, level="warning"), raw("c", 1, level="warning"),
            raw("d", 1, level="other"), raw("e", 1, level="weird")]
    assert severity_counts(rows) == {"critical": 1, "warning": 2, "other": 2}


def test_handover_has_start_and_keeps_duration_desc():
    ref = NOW
    occs = [Occ(ref - timedelta(hours=12), None, "X", "APP1", "", None),
            Occ(ref - timedelta(hours=40), None, "Y", "APP2", "", None),
            Occ(ref - timedelta(hours=20), None, "Z", "APP3", "", None)]
    items, _ = handover(occs, ref, 10, "-", 10, TZ)
    assert [i["application"] for i in items] == ["APP2", "APP3", "APP1"]  # thời gian tồn giảm dần
    assert items[0]["start_label"] == format_start(ref - timedelta(hours=40), TZ)
