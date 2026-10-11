import time, requests
import pandas as pd

URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
VARS = ["temperature_2m", "relative_humidity_2m", "precipitation",
        "wind_speed_10m", "wind_direction_10m", "pressure_msl", "cloud_cover"]
hourly = ",".join(VARS + [f"{v}_previous_day1" for v in VARS])

frames = []
for start, end in [("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-12-31"),
                   ("2026-01-01", "2026-10-10")]:
    r = requests.get(URL, params={
        "latitude": 13.7563, "longitude": 100.5018,      # central Bangkok
        "start_date": start, "end_date": end,
        "hourly": hourly, "timezone": "Asia/Bangkok"}, timeout=120)
    if r.status_code != 200:
        print(r.status_code, r.text[:500]); raise SystemExit(1)
    h = r.json()["hourly"]
    frames.append(pd.DataFrame(h))
    print(f"{start} to {end}: {len(h['time'])} hours")
    time.sleep(2)

df = pd.concat(frames)
df.to_csv("data/weather_hourly.csv", index=False)
print(f"\nsaved {len(df)} rows to data/weather_hourly.csv")

# How wrong is the day-ahead forecast? (this is why training on actual weather is leakage)
for v in ["temperature_2m", "relative_humidity_2m", "wind_speed_10m"]:
    err = (df[f"{v}_previous_day1"] - df[v]).abs()
    print(f"  {v:<22} mean abs forecast error: {err.mean():.2f}   "
          f"missing forecasts: {df[f'{v}_previous_day1'].isna().mean():.1%}")