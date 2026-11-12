import pytest

from trucca.timeutil import format_duration, parse_am_time


@pytest.mark.parametrize("seconds,expected", [
    (0, "0.0h"),
    (59, "0.0h"),
    (360, "0.1h"),
    (12.7 * 3600, "12.7h"),
    (23 * 3600, "23.0h"),
    (23.99 * 3600, "23.9h"),          # làm tròn xuống, không thành "24.0h"
    (24 * 3600, "1d 0h"),
    (31 * 3600 + 1800, "1d 7h"),
    ((8 * 24 + 9) * 3600 + 3599, "8d 9h"),
    (-5, "0.0h"),
    (None, "--"),
])
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_parse_am_time_nanoseconds_and_zero():
    dt = parse_am_time("2026-11-11T10:00:00.123456789Z")
    assert dt.isoformat() == "2026-11-11T10:00:00.123456+00:00"
    assert parse_am_time("0001-01-01T00:00:00Z") is None
    assert parse_am_time(None) is None
    assert parse_am_time("2026-11-11T17:00:00+07:00").hour == 10
