import pandas as pd
from src import checks
from pathlib import Path


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


FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    df = pd.read_csv(FIXTURES / name)
    return pd.Series(df["pm25"].values, index=pd.to_datetime(df["time"], utc=True))


def test_real_incident_bang_sue_sep_2023_is_flagged():
    # Air4Thai Bang Sue reported exactly 15.0 for most of September 2023 (data-findings, Finding 2)
    s = load("bang_sue_frozen_2023-09.csv")
    flagged = checks.stuck_mask(s)
    assert flagged.any(), "the real Bang Sue freeze was not detected"
    assert (s[flagged] == 15.0).all(), "flagged hours should be the frozen 15.0 readings"


def test_healthy_station_same_period_is_not_flagged():
    s = load("din_daeng_healthy_2023-09.csv")
    assert not checks.stuck_mask(s).any()