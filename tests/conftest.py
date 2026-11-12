from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from trucca.config import ShiftConfig

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
UTC = UTC


def vn(y, m, d, hh=0, mm=0, ss=0):
    """Thời điểm giờ Việt Nam -> datetime UTC."""
    return datetime(y, m, d, hh, mm, ss, tzinfo=TZ).astimezone(UTC)


@pytest.fixture
def shifts():
    return [ShiftConfig(name="CA_1", start=time(8), end=time(18)),
            ShiftConfig(name="CA_2", start=time(18), end=time(8))]
