import numpy as np
import pandas as pd

from src import features
from src.features import FEATURES, WEATHER

DAYS = pd.date_range("2025-01-01", "2025-01-10").strftime("%Y-%m-%d")
T = pd.DataFrame({"location_id": 1, "date": DAYS, "pm25": np.arange(10, 20, dtype=float)})
W = pd.DataFrame({"date": pd.date_range("2025-01-01", "2025-01-11").strftime("%Y-%m-%d")})
for i, col in enumerate(WEATHER):
    W[col] = np.arange(11, dtype=float) + 100 * i
S = pd.DataFrame({"location_id": [1], "lat": [13.7], "lon": [100.5]})


def row(df, day):
    return df[df["date"] == pd.Timestamp(day)].iloc[0]


def test_inputs_for_day_d_ignore_day_d_and_everything_later():
    base = features.build(T, W, S)
    later_t = T.copy()
    later_t.loc[later_t["date"] >= "2025-01-06", "pm25"] += 100   # change D and after
    later_w = W.copy()
    later_w.loc[later_w["date"] > "2025-01-06", WEATHER] += 100   # change weather after D
    changed = features.build(later_t, later_w, S)

    a = row(base, "2025-01-06")[FEATURES].astype(float).values
    b = row(changed, "2025-01-06")[FEATURES].astype(float).values
    assert np.allclose(a, b, equal_nan=True), "an input for day D used information from D or later"
    assert row(changed, "2025-01-06")["y"] != row(base, "2025-01-06")["y"]   # only the answer moved


def test_lags_are_calendar_days_not_previous_rows():
    gappy = T[T["date"] != "2025-01-05"]           # station missing Jan 5
    out = features.build(gappy, W, S)
    assert pd.Timestamp("2025-01-06") not in set(out["date"])   # no value yesterday -> no row
    r = row(out, "2025-01-07")
    assert r["lag1"] == 15.0                       # Jan 6
    assert np.isnan(r["lag2"])                     # Jan 5 is missing, NOT Jan 4's value


def test_includes_the_day_to_forecast():
    out = features.build(T, W, S)
    r = row(out, "2025-01-11")                     # one day after the last target
    assert np.isnan(r["y"])
    assert r["lag1"] == 19.0
    assert r["w_temp_mean"] == 10.0                # its weather forecast is there