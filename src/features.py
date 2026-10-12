"""Model inputs for forecasting day D, using only what is known at 01:00 on day D.

Row = (station, day D). y = PCD 24-h average for day D (src/target.py).
  lag1, lag2, lag3   the station's value on D-1, D-2, D-3 (calendar days; gaps stay empty)
  mean7              average of D-7..D-1 (needs at least 4 of the 7 days)
  city_lag1, city_n  average over all stations on D-1, and how many stations reported
  lat, lon           station location
  doy_sin, doy_cos   season (day of year on a circle, so 31 Dec is next to 1 Jan)
  dow                day of week (traffic)
  w_*                day-2 weather forecast for day D (src/weather.py)
Rows without lag1 are dropped: no forecast is published for a station with no value yesterday.
The output includes one day past the last target (y empty): that is the day to forecast.
"""
import numpy as np
import pandas as pd

WEATHER = ["w_temp_mean", "w_rh_mean", "w_precip_sum", "w_wind_mean", "w_wind_u", "w_wind_v"]
FEATURES = ["lag1", "lag2", "lag3", "mean7", "city_lag1", "city_n",
            "lat", "lon", "doy_sin", "doy_cos", "dow"] + WEATHER


def _long(wide: pd.DataFrame, name: str) -> pd.DataFrame:
    out = wide.rename_axis(index="date", columns="location_id").reset_index()
    return out.melt(id_vars="date", var_name="location_id", value_name=name)


def build(targets: pd.DataFrame, weather_daily: pd.DataFrame,
          stations: pd.DataFrame) -> pd.DataFrame:
    t = targets.assign(date=pd.to_datetime(targets["date"]))
    wide = t.pivot_table(index="date", columns="location_id", values="pm25")
    days = pd.date_range(wide.index.min(), wide.index.max() + pd.Timedelta(days=1), freq="D")
    wide = wide.reindex(days)                  # every calendar day, gaps become empty

    yesterday = wide.shift(1)
    parts = {"y": wide, "lag1": yesterday, "lag2": wide.shift(2), "lag3": wide.shift(3),
             "mean7": yesterday.rolling(7, min_periods=4).mean()}
    df = None
    for name, w in parts.items():
        piece = _long(w, name)
        df = piece if df is None else df.merge(piece, on=["date", "location_id"])
    df["location_id"] = df["location_id"].astype(int)

    city = pd.DataFrame({"date": days,
                         "city_lag1": yesterday.mean(axis=1).values,
                         "city_n": yesterday.count(axis=1).values})
    df = df.merge(city, on="date")
    df = df.merge(stations[["location_id", "lat", "lon"]], on="location_id", how="left")

    doy = df["date"].dt.dayofyear
    df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    df["dow"] = df["date"].dt.dayofweek

    w = weather_daily.assign(date=pd.to_datetime(weather_daily["date"]))[["date"] + WEATHER]
    df = df.merge(w, on="date", how="left")

    df = df[df["lag1"].notna()]
    return df.sort_values(["date", "location_id"]).reset_index(drop=True)