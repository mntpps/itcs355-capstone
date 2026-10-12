from datetime import date

import pandas as pd
import pytest

from src import target

TZ = "Asia/Bangkok"


def series(days):
    """days: list of 24-value lists (None = missing hour), starting 2025-01-01."""
    start = pd.Timestamp("2025-01-01", tz=TZ)
    idx, vals = [], []
    for d, hours in enumerate(days):
        for hr, v in enumerate(hours):
            if v is not None:
                idx.append(start + pd.Timedelta(days=d, hours=hr))
                vals.append(v)
    return pd.Series(vals, index=pd.DatetimeIndex(idx))


def varying(base):
    """24 different values, like a healthy moving average."""
    return [base + 0.1 * i for i in range(24)]


def test_uses_the_23h_reading():
    out = target.daily_target(series([varying(10)]))
    assert len(out) == 1
    assert out["pm25"].iloc[0] == pytest.approx(12.3)
    assert out["source"].iloc[0] == "23h"


def test_falls_back_to_22h():
    day = varying(10)
    day[23] = None
    out = target.daily_target(series([day]))
    assert out["pm25"].iloc[0] == pytest.approx(12.2)
    assert out["source"].iloc[0] == "22h"


def test_no_target_without_22h_or_23h():
    day = varying(10)
    day[22] = day[23] = None
    assert target.daily_target(series([day])).empty


def test_readings_within_24h_of_a_freeze_are_dropped():
    day2 = varying(20)
    day2[12:22] = [15.0] * 10            # frozen 12:00-21:00 on Jan 2
    out = target.daily_target(series([varying(10), day2, varying(30)]))
    # Jan 1 23:00 is 13 h before the freeze, Jan 2 23:00 is 2 h after: both dropped.
    # Jan 3 23:00 is 26 h after the last frozen hour: kept.
    assert list(out["date"]) == [date(2025, 1, 3)]


def test_implausible_value_is_dropped():
    day = varying(10)
    day[23] = 0.0
    assert target.daily_target(series([day])).empty