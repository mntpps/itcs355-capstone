"""Day-ahead weather inputs from the Open-Meteo Previous Runs API (CC BY 4.0).

Uses <variable>_previous_day2: the forecast made 48 h before each hour. At 01:00 on day D
every hour of day D already has a published day-2 forecast, so training and the nightly
job use the same definition and no input comes from after 01:00. (Day 1 would not: for the
evening hours, the run 24 h earlier is published after 01:00.) Fallback: _previous_day3.

    python -m src.weather download --start 2024-01-01
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
LAT, LON = 13.75, 100.52          # central Bangkok: one point for the whole city
TZ = "Asia/Bangkok"
VARIABLES = ["temperature_2m", "relative_humidity_2m", "precipitation",
             "wind_speed_10m", "wind_direction_10m"]
LEADS = (2, 3)                    # day 2 first, day 3 if missing
HOURLY = Path("data/weather_hourly.csv")
DAILY = Path("data/weather_daily.csv")


def fetch_hourly(start: str, end: str, tries: int = 5) -> pd.DataFrame:
    params = {"latitude": LAT, "longitude": LON, "timezone": TZ,
              "start_date": start, "end_date": end,
              "hourly": ",".join(f"{v}_previous_day{d}" for v in VARIABLES for d in LEADS)}
    for attempt in range(tries):
        try:
            r = requests.get(URL, params=params, timeout=120)
        except requests.RequestException as e:
            r, reason = None, type(e).__name__
        else:
            if r.status_code not in {429, 500, 502, 503, 504}:
                r.raise_for_status()
                df = pd.DataFrame(r.json()["hourly"])
                df["time"] = pd.to_datetime(df["time"])      # Bangkok local time
                return df
            reason = r.status_code
        wait = 5 * 2 ** attempt
        print(f"  retry {attempt + 1}/{tries} in {wait}s ({reason})")
        time.sleep(wait)
    raise RuntimeError("Open-Meteo request failed")


def daily_features(hourly: pd.DataFrame) -> pd.DataFrame:
    """Hourly forecasts -> one row per date of weather inputs (w_ prefix)."""
    df = pd.DataFrame({"date": hourly["time"].dt.date})
    for v in VARIABLES:
        df[v] = hourly[f"{v}_previous_day2"].fillna(hourly[f"{v}_previous_day3"])
    # Wind as east-west (u) and north-south (v) parts: averaging raw directions is
    # meaningless (350° and 10° would average to 180°). Direction = where wind comes FROM.
    rad = np.deg2rad(df["wind_direction_10m"])
    df["wind_u"] = -df["wind_speed_10m"] * np.sin(rad)
    df["wind_v"] = -df["wind_speed_10m"] * np.cos(rad)
    g = df.groupby("date")
    return pd.DataFrame({
        "w_temp_mean": g["temperature_2m"].mean(),
        "w_rh_mean": g["relative_humidity_2m"].mean(),
        "w_precip_sum": g["precipitation"].sum(min_count=1),
        "w_wind_mean": g["wind_speed_10m"].mean(),
        "w_wind_u": g["wind_u"].mean(),
        "w_wind_v": g["wind_v"].mean(),
        "w_hours": g["temperature_2m"].count(),     # how many hours had a forecast
    }).reset_index()


def download(start: str, end: str) -> None:
    years = range(int(start[:4]), int(end[:4]) + 1)
    parts = []
    for y in years:                                   # one request per year, as for OpenAQ
        a = max(start, f"{y}-01-01")
        b = min(end, f"{y}-12-31")
        parts.append(fetch_hourly(a, b))
        print(f"{a} .. {b}: {len(parts[-1]):,} hours")
    hourly = pd.concat(parts, ignore_index=True)
    HOURLY.parent.mkdir(parents=True, exist_ok=True)
    hourly.to_csv(HOURLY, index=False)
    daily = daily_features(hourly)
    daily.to_csv(DAILY, index=False)

    for d in LEADS:
        missing = hourly[f"temperature_2m_previous_day{d}"].isna().mean()
        print(f"temperature day-{d} forecast missing: {missing:.1%}")
    print(f"wrote {DAILY}: {len(daily):,} days; days with < 24 forecast hours: "
          f"{(daily['w_hours'] < 24).sum()}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--start", default="2024-01-01")
    d.add_argument("--end", default=pd.Timestamp.now(tz=TZ).strftime("%Y-%m-%d"))
    args = ap.parse_args()
    download(args.start, args.end)


if __name__ == "__main__":
    main()