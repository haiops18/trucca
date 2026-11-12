from datetime import date, timedelta

from tests.conftest import TZ, vn
from trucca.reports.calc import Occ, build_report, compare, handover, shift_totals, top_n, trend, trend_last_day
from trucca.timeutil import shift_window


def occ(start, end=None, name="A", app="APP", inst=None, summary=""):
    return Occ(start, end, name, app, summary, inst)


def ca2(shifts, d=date(2026, 11, 11)):
    return shift_window(shifts[1], d, TZ)


def test_overnight_counts(shifts):
    w = ca2(shifts)
    occs = [
        occ(vn(2026, 11, 11, 17, 59)),                          # trước ca, còn active => tồn
        occ(vn(2026, 11, 11, 18, 0)),                           # đúng mốc bắt đầu => phát sinh
        occ(vn(2026, 11, 11, 23, 0), vn(2026, 11, 12, 1, 0)),   # phát sinh + clear trong ca (qua nửa đêm)
        occ(vn(2026, 11, 12, 2, 0), vn(2026, 11, 12, 9, 0)),    # phát sinh trong ca, clear sau ca => tồn
        occ(vn(2026, 11, 12, 8, 0)),                            # đúng mốc kết thúc => KHÔNG thuộc ca
        occ(vn(2026, 11, 10, 9, 0), vn(2026, 11, 11, 19, 0)),   # phát sinh ca trước, clear trong ca
        occ(vn(2026, 11, 11, 10, 0), vn(2026, 11, 11, 12, 0)),  # hoàn toàn ngoài ca
    ]
    t = shift_totals(occs, w, now=vn(2026, 11, 12, 9))
    assert t == {"new": 3, "cleared": 2, "carried_over": 3}


def test_carried_over_hidden_until_shift_ends(shifts):
    w = ca2(shifts)
    occs = [occ(vn(2026, 11, 11, 20))]
    assert shift_totals(occs, w, now=vn(2026, 11, 12, 7, 59))["carried_over"] is None
    assert shift_totals(occs, w, now=vn(2026, 11, 12, 8, 0))["carried_over"] == 1


def test_resolved_exactly_at_end_still_carried(shifts):
    w = ca2(shifts)
    o = occ(vn(2026, 11, 11, 20), vn(2026, 11, 12, 8, 0))
    t = shift_totals([o], w, now=vn(2026, 11, 12, 9))
    assert t["carried_over"] == 1 and t["cleared"] == 0


def test_top_n_ranking_and_percent():
    values = ["X"] * 12 + ["Y"] * 9 + ["Z"] * 6 + ["W"] * 5 + ["V"] * 5 + ["U"] * 3
    top = top_n(values, 5, total=97)
    assert [t["name"] for t in top] == ["X", "Y", "Z", "V", "W"]  # cùng số lượng => theo tên
    assert top[0] == {"rank": 1, "name": "X", "count": 12, "total": 97, "pct": 12.4, "bar_pct": 100.0}
    assert top[1]["bar_pct"] == 75.0
    assert top_n([], 5) == []


def test_compare_with_yesterday():
    assert compare(97, 75) == {"value": 97, "yesterday": 75, "delta": 22, "trend": "up", "bar_pct": 100.0}
    c = compare(50, 100)
    assert c["delta"] == -50 and c["trend"] == "down" and c["bar_pct"] == 50.0
    assert compare(0, 0)["trend"] == "flat"
    assert compare(None, 10)["value"] is None


def test_handover_grouping_and_threshold():
    ref = vn(2026, 11, 12, 8)
    occs = [
        occ(ref - timedelta(days=8, hours=9), name="APISIXTargetDown", app="APP_A", summary="APISIXTargetDown"),
        occ(ref - timedelta(hours=31), name="Down", app="APP_B", inst="10.0.0.39:9104"),
        occ(ref - timedelta(hours=20), name="Down", app="APP_B", inst="10.0.0.38:9104"),
        occ(ref - timedelta(hours=11), name="Down", app="APP_B", inst="10.0.0.40:9104"),
        occ(ref - timedelta(hours=9, minutes=59), name="Short", app="X"),        # dưới ngưỡng
        occ(ref - timedelta(hours=30), ref - timedelta(hours=1), name="Gone"),   # đã clear trước mốc
    ]
    items, more = handover(occs, ref, 10, "(chưa gán)", limit=10)
    assert more == 0
    assert [(i["application"], i["count"], i["duration"]) for i in items] == [("APP_A", 1, "8d 9h"), ("APP_B", 3, "1d 7h")]
    assert items[1]["summary"] == "(3) 10.0.0.38:9104 – 10.0.0.40:9104"
    items, more = handover(occs, ref, 10, "(chưa gán)", limit=1)
    assert len(items) == 1 and more == 1


def test_trend_for_overnight_shift_ends_on_next_day(shifts):
    w = ca2(shifts)
    assert trend_last_day(w, TZ) == date(2026, 11, 12)
    occs = [occ(vn(2026, 11, 6, 1)), occ(vn(2026, 11, 12, 7, 59)), occ(vn(2026, 11, 12, 9)), occ(vn(2026, 11, 5, 23))]
    t = trend(occs, trend_last_day(w, TZ), 7, w.end, TZ)
    assert [x["label"] for x in t] == ["06/11", "07/11", "08/11", "09/11", "10/11", "11/11", "12/11"]
    # 12/11 chỉ tính tới 08:00 (kết thúc ca); 05/11 nằm ngoài 7 ngày
    assert [x["count"] for x in t] == [1, 0, 0, 0, 0, 0, 1]


def test_build_report_empty_data(shifts):
    w = ca2(shifts)
    y = ca2(shifts, date(2026, 11, 10))
    r = build_report(occs=[], window=w, yesterday=y, now=vn(2026, 11, 11, 20), tz=TZ, title="T", top_count=5,
                     trend_days=7, handover_threshold_hours=10, handover_limit=15, missing_text="(chưa gán)")
    assert r["totals"] == {"new": 0, "cleared": 0, "carried_over": None}
    assert r["finished"] is False and r["top_applications"] == [] and len(r["trend"]) == 7
    assert r["compare"]["new"]["delta"] == 0


def test_build_report_compare_uses_yesterday_same_shift(shifts):
    w = ca2(shifts)
    y = ca2(shifts, date(2026, 11, 10))
    occs = [occ(vn(2026, 11, 10, 19)), occ(vn(2026, 11, 11, 19)), occ(vn(2026, 11, 11, 21)), occ(vn(2026, 11, 11, 9))]
    r = build_report(occs=occs, window=w, yesterday=y, now=vn(2026, 11, 13), tz=TZ, title="T", top_count=5,
                     trend_days=7, handover_threshold_hours=10, handover_limit=15, missing_text="-",
                     yesterday_carried_override=5)
    assert r["compare"]["new"]["value"] == 2 and r["compare"]["new"]["yesterday"] == 1
    assert r["compare"]["carried_over"]["yesterday"] == 5
