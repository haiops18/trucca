from datetime import UTC, datetime, timedelta

from trucca.collector import (
    FetchedAlert,
    KnownAlert,
    normalize_alert,
    passes_filters,
    passes_suppression,
    plan_reconcile,
)
from trucca.config import FiltersConfig, LabelsConfig

UTC = UTC
NOW = datetime(2026, 11, 12, 3, 0, tzinfo=UTC)


def fa(fp, starts=NOW - timedelta(hours=1), ends=None, summary="s"):
    return FetchedAlert(fp, "A", summary, None, None, None, None, {"alertname": "A"}, {"summary": summary},
                        starts, ends, "active")


def ka(fp, status="active", starts=NOW - timedelta(hours=1), missing=0, first_missing=None):
    return KnownAlert(fp, status, starts, 10, missing, first_missing, "s", {"alertname": "A"}, {"summary": "s"}, "active")


def test_new_and_seen():
    p = plan_reconcile([fa("a"), fa("b")], set(), {"a": ka("a")}, NOW, 2)
    assert [a.fingerprint for a in p.new] == ["b"] and p.seen == ["a"] and not p.changed


def test_missing_requires_consecutive_polls():
    p = plan_reconcile([], set(), {"a": ka("a")}, NOW, 2)
    assert p.missing == [("a", 1, NOW)] and not p.resolve
    later = NOW + timedelta(seconds=30)
    p = plan_reconcile([], set(), {"a": ka("a", missing=1, first_missing=NOW)}, later, 2)
    assert p.resolve == [("a", NOW, "api_missing")]  # resolved_at = lần vắng mặt đầu tiên


def test_reappearing_resets_missing():
    p = plan_reconcile([fa("a")], set(), {"a": ka("a", missing=1, first_missing=NOW)}, NOW, 2)
    assert p.seen == ["a"] and not p.missing and not p.resolve


def test_filtered_out_waits_for_polls_and_keeps_source():
    p = plan_reconcile([], {"a"}, {"a": ka("a")}, NOW, 2)
    assert p.missing == [("a", 1, NOW)] and not p.resolve
    p = plan_reconcile([], {"a"}, {"a": ka("a", missing=1, first_missing=NOW)}, NOW, 2)
    assert p.resolve == [("a", NOW, "filtered_out")]


def test_invalid_item_is_not_counted_missing():
    p = plan_reconcile([], set(), {"a": ka("a", missing=1, first_missing=NOW)}, NOW, 2, present_invalid={"a"})
    assert not p.resolve and not p.missing


def test_refire_that_already_ended_records_new_occurrence():
    ended = NOW - timedelta(seconds=5)
    p = plan_reconcile([fa("a", starts=NOW - timedelta(seconds=20), ends=ended)], set(), {"a": ka("a")}, NOW, 2)
    assert len(p.refire_ended) == 1 and not p.resolve


def test_refire_between_polls_creates_new_occurrence():
    p = plan_reconcile([fa("a", starts=NOW - timedelta(minutes=1))], set(), {"a": ka("a")}, NOW, 2)
    assert len(p.refire) == 1 and not p.new


def test_resolved_then_firing_again_is_new():
    p = plan_reconcile([fa("a", starts=NOW - timedelta(minutes=5))], set(), {"a": ka("a", status="resolved")}, NOW, 2)
    assert [a.fingerprint for a in p.new] == ["a"]


def test_same_starts_after_resolve_is_reopen():
    p = plan_reconcile([fa("a")], set(), {"a": ka("a", status="resolved")}, NOW, 2)
    assert len(p.reopen) == 1 and not p.new


def test_ends_at_in_past_resolves():
    ended = NOW - timedelta(minutes=2)
    p = plan_reconcile([fa("a", ends=ended)], set(), {"a": ka("a")}, NOW, 2)
    assert p.resolve == [("a", ended, "ends_at")]
    p = plan_reconcile([fa("b", ends=ended)], set(), {}, NOW, 2)
    assert len(p.new_resolved) == 1


def test_content_change_detected():
    p = plan_reconcile([fa("a", summary="new")], set(), {"a": ka("a")}, NOW, 2)
    assert [a.fingerprint for a in p.changed] == ["a"]


def test_empty_response_never_clears_immediately():
    # Lỗi kết nối được xử lý ở Collector._poll (return sớm); plan với danh sách rỗng chỉ
    # tăng bộ đếm vắng mặt, không clear ngay => an toàn ngay cả khi Alertmanager trả []
    known = {f"x{i}": ka(f"x{i}") for i in range(100)}
    p = plan_reconcile([], set(), known, NOW, 2)
    assert not p.resolve and len(p.missing) == 100


def test_normalize_and_filters():
    raw = {"fingerprint": "f1", "labels": {"alertname": "Disk", "application": "APP", "team": "x", "owner": " "},
           "annotations": {"summary": "full"}, "startsAt": "2026-11-11T10:00:00Z", "endsAt": "2026-11-11T11:00:00Z",
           "status": {"state": "suppressed", "silencedBy": ["s1"], "inhibitedBy": []}}
    a = normalize_alert(raw, LabelsConfig())
    assert a.alertname == "Disk" and a.application == "APP" and a.owner is None and a.summary == "full"
    assert not passes_suppression(raw, FiltersConfig())
    assert passes_suppression(raw, FiltersConfig(include_silenced=True))
    assert passes_filters(a, FiltersConfig(match={"team": ["x", "y"]}))
    assert not passes_filters(a, FiltersConfig(match={"team": "y"}))
    assert not passes_filters(a, FiltersConfig(exclude_alertnames=["Disk"]))
    assert passes_filters(a, FiltersConfig(match_regex={"application": "AP.*"}))
    assert normalize_alert({"labels": {}}, LabelsConfig()) is None


def test_mass_disappearance_waits_longer():
    known = {f"x{i}": ka(f"x{i}", missing=2, first_missing=NOW) for i in range(20)}
    known["keep"] = ka("keep")
    guard = (0.5, 10, 10)
    p = plan_reconcile([fa("keep")], set(), known, NOW, 2, guard)
    assert p.mass_disappearance and not p.resolve and len(p.missing) == 20
    known = {f"x{i}": ka(f"x{i}", missing=9, first_missing=NOW) for i in range(20)}
    p = plan_reconcile([], set(), known, NOW, 2, guard)
    assert len(p.resolve) == 20 and p.resolve[0][1] == NOW


def test_small_disappearance_not_guarded():
    known = {f"x{i}": ka(f"x{i}") for i in range(30)}
    known["gone"] = ka("gone", missing=1, first_missing=NOW)
    p = plan_reconcile([fa(f"x{i}") for i in range(30)], set(), known, NOW, 2, (0.5, 10, 10))
    assert not p.mass_disappearance and p.resolve == [("gone", NOW, "api_missing")]


def test_bad_timestamp_is_invalid_not_crash():
    raw = {"fingerprint": "f", "labels": {"alertname": "x"}, "startsAt": "2026-11-11T10:00:00Z", "endsAt": "garbage"}
    assert normalize_alert(raw, LabelsConfig()) is None
    raw["endsAt"] = 123
    assert normalize_alert(raw, LabelsConfig()) is None
