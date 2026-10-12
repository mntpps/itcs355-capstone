import numpy as np
import pandas as pd
import pytest

from src import weather


def hourly(n=24, **overrides):
    """n hours on 2025-01-01, every variable/lead filled with 1.0 unless overridden."""
    df = pd.DataFrame({"time": pd.date_range("2025-01-01", periods=n, freq="h")})
    for v in weather.VARIABLES:
        for d in weather.LEADS:
            df[f"{v}_previous_day{d}"] = 1.0
    for col, values in overrides.items():
        df[col] = values
    return df


def test_day3_fills_in_when_day2_is_missing():
    h = hourly(temperature_2m_previous_day2=np.nan, temperature_2m_previous_day3=30.0)
    out = weather.daily_features(h)
    assert out["w_temp_mean"].iloc[0] == pytest.approx(30.0)
    assert out["w_hours"].iloc[0] == 24


def test_precipitation_is_summed_not_averaged():
    h = hourly(precipitation_previous_day2=0.5)
    assert weather.daily_features(h)["w_precip_sum"].iloc[0] == pytest.approx(12.0)


def test_north_wind_blows_south():
    # wind FROM the north (0°) at 10 km/h moves air southward: v = -10, u = 0
    h = hourly(wind_speed_10m_previous_day2=10.0, wind_direction_10m_previous_day2=0.0)
    out = weather.daily_features(h)
    assert out["w_wind_v"].iloc[0] == pytest.approx(-10.0)
    assert out["w_wind_u"].iloc[0] == pytest.approx(0.0, abs=1e-9)


def test_no_forecast_at_all_gives_empty_values_not_zero():
    h = hourly(precipitation_previous_day2=np.nan, precipitation_previous_day3=np.nan)
    assert np.isnan(weather.daily_features(h)["w_precip_sum"].iloc[0])