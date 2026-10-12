"""Are Air4Thai 'hourly' values really 24-hour moving averages?

If yes: hour-to-hour changes are tiny, neighbouring hours are almost identical, and
even the dry season (strongest real daily cycle) shows no day/night pattern.
"""
from pathlib import Path
import numpy as np
import pandas as pd

TZ = "Asia/Bangkok"
parts = []
for f in Path("data/hourly").glob("*/*.csv"):
    d = pd.read_csv(f)
    if len(d):
        d["location_id"] = int(f.parent.name)
        parts.append(d)
h = pd.concat(parts, ignore_index=True)
h["time"] = pd.to_datetime(h["time_utc"], utc=True).dt.tz_convert(TZ)
h = h.sort_values(["location_id", "time"])

# 1. Hour-to-hour change (only between truly consecutive hours)
g = h.groupby("location_id")
step = g["pm25"].diff()
consecutive = g["time"].diff() == pd.Timedelta(hours=1)
hourly_change = step[consecutive].abs()

# 2. Day-to-day change of the daily mean
h["date"] = h["time"].dt.date
daily = h.groupby(["location_id", "date"])["pm25"].mean().reset_index()
daily_change = daily.groupby("location_id")["pm25"].diff().abs().dropna()

print("typical change (µg/m³)        median    p95")
print(f"  hour to next hour         {hourly_change.median():>7.2f} {hourly_change.quantile(.95):>7.2f}")
print(f"  day to next day           {daily_change.median():>7.2f} {daily_change.quantile(.95):>7.2f}")

# 3. How alike are neighbouring hours? (lag-1 correlation, averaged over stations)
r = g["pm25"].apply(lambda s: s.autocorr(1)).mean()
print(f"\ncorrelation between one hour and the next: {r:.3f}")

# 4. Daily cycle in the dry season only (Jan-Feb), where a real cycle is strongest
dry = h[h["time"].dt.month.isin([1, 2])]
cyc = dry.groupby(dry["time"].dt.hour)["pm25"].mean()
print(f"\ndry-season (Jan-Feb) cycle: lowest {cyc.min():.1f} at {cyc.idxmin():02d}h, "
      f"highest {cyc.max():.1f} at {cyc.idxmax():02d}h, swing {100*(cyc.max()/cyc.min()-1):.0f}%")

# 5. One real day, so you can see it with your own eyes
s = h[h["location_id"] == h["location_id"].iloc[0]]
day = s[s["time"].dt.strftime("%Y-%m-%d") == "2025-01-15"]
print(f"\nstation {s['location_id'].iloc[0]}, 2025-01-15, hour by hour:")
print("  " + "  ".join(f"{t:%H}h {v:.1f}" for t, v in zip(day["time"], day["pm25"])))