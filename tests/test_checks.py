import pandas as pd
from src import checks


def hours(values, start="2024-01-01 00:00"):
    idx = pd.date_range(start, periods=len(values), freq="h", tz="Asia/Bangkok")
    return pd.Series(values, index=idx, dtype=float)


def test_healthy_run_of_7_hours_is_not_flagged():
    # 7 h was the longest identical run at any healthy station in 2024
    s = hours([10, 11] + [12] * 7 + [13, 14])
    assert not checks.stuck_mask(s).any()


def test_run_of_8_hours_is_flagged_and_only_those_hours():
    s = hours([10] + [15] * 8 + [11])
    m = checks.stuck_mask(s)
    assert m.sum() == 8
    assert not m.iloc[0] and not m.iloc[-1]


def test_missing_hour_breaks_a_run():
    full = hours([15] * 10)
    s = full.drop(full.index[5])          # runs of 5 and 4, not one run of 9
    assert not checks.stuck_mask(s).any()


def test_zero_day_is_implausible_but_a_clean_day_is_not():
    daily = pd.Series([0.0, 0.5, 3.0, 25.0])
    assert checks.implausible_mask(daily).tolist() == [True, True, False, False]


def test_feed_outage_when_fewer_than_half_report():
    assert checks.feed_outage(30, 80)
    assert not checks.feed_outage(60, 80)