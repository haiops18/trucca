from datetime import date, timedelta

from tests.conftest import TZ, vn
from trucca.timeutil import current_shift, finished_shifts_between, shift_window


def test_day_shift_window(shifts):
    w = shift_window(shifts[0], date(2026, 11, 11), TZ)
    assert w.start == vn(2026, 11, 11, 8)
    assert w.end == vn(2026, 11, 11, 18)


def test_overnight_shift_ends_next_day(shifts):
    w = shift_window(shifts[1], date(2026, 11, 11), TZ)
    assert w.start == vn(2026, 11, 11, 18)
    assert w.end == vn(2026, 11, 12, 8)
    assert w.end - w.start == timedelta(hours=14)


def test_current_shift_after_midnight_belongs_to_previous_day(shifts):
    w = current_shift(shifts, vn(2026, 11, 12, 3, 0), TZ)
    assert w.name == "CA_2" and w.report_date == date(2026, 11, 11)


def test_current_shift_boundaries(shifts):
    assert current_shift(shifts, vn(2026, 11, 12, 7, 59, 59), TZ).name == "CA_2"
    w = current_shift(shifts, vn(2026, 11, 12, 8, 0), TZ)
    assert w.name == "CA_1" and w.report_date == date(2026, 11, 12)
    w = current_shift(shifts, vn(2026, 11, 12, 18, 0), TZ)
    assert w.name == "CA_2" and w.report_date == date(2026, 11, 12)


def test_finished_shifts_between(shifts):
    got = finished_shifts_between(shifts, vn(2026, 11, 11, 17, 0), vn(2026, 11, 12, 8, 0), TZ)
    assert [(w.name, w.report_date) for w in got] == [("CA_1", date(2026, 11, 11)), ("CA_2", date(2026, 11, 11))]
    # Biên trái loại trừ: since == end thì không tính lại
    got = finished_shifts_between(shifts, vn(2026, 11, 12, 8, 0), vn(2026, 11, 12, 9, 0), TZ)
    assert got == []
