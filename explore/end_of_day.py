"""Daily target = Air4Thai's own 24-h average at the end of the day (Finding 7).

Target for day D: the 23:00 reading; if missing, 22:00; if missing, 21:00; else no target.
Days touched by a stuck sensor (that day or the day before) are dropped: the moving
average slides toward the frozen value for 24 h before it goes flat.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from src import checks

TZ = "Asia/Bangkok"
parts = []
for f in Path("data/hourly").glob("*/*.csv"):
    d = pd.read_csv(f)
    if len(d):
        d["location_id"] = int(f.parent.name)
        parts.append(d)
h = pd.concat(parts, ignore_index=True)
h["time"] = pd.to_datetime(h["time_utc"], utc=True).dt.tz_convert(TZ)
h = h[h["time"] < pd.Timestamp.now(tz=TZ).normalize()]
h = h.sort_values(["location_id", "time"]).reset_index(drop=True)
h["date"] = h["time"].dt.date
h["hour"] = h["time"].dt.hour

# Stuck hours, per station
h["stuck"] = False
for loc, idx in h.groupby("location_id").groups.items():
    s = pd.Series(h.loc[idx, "pm25"].values, index=pd.DatetimeIndex(h.loc[idx, "time"]))
    h.loc[idx, "stuck"] = checks.stuck_mask(s).values
stuck_days = set(map(tuple, h.loc[h["stuck"], ["location_id", "date"]].drop_duplicates().values))
print(f"stuck hours: {h['stuck'].sum():,}   station-days touched: {len(stuck_days):,}")

# End-of-day readings
all_days = h.groupby(["location_id", "date"]).size().index
eod = (h[~h["stuck"] & h["hour"].isin([21, 22, 23])]
       .pivot_table(index=["location_id", "date"], columns="hour", values="pm25")
       .reindex(index=all_days, columns=[21, 22, 23]))
source = np.select([eod[23].notna(), eod[22].notna(), eod[21].notna()],
                   ["23h", "22h", "21h"], "none")
print("\nwhere the daily value comes from (station-days with any data):")
for k, v in pd.Series(source).value_counts().items():
    print(f"  {k:>5}: {v:>7,}  ({v/len(source):.0%})")

for hr in [22, 21]:
    both = eod[[hr, 23]].dropna()
    e = (both[hr] - both[23]).abs()
    print(f"cost of using {hr}h instead of 23h: median {e.median():.2f}, p95 {e.quantile(.95):.2f} µg/m³")

# Target table
t = eod[23].fillna(eod[22]).fillna(eod[21]).dropna().rename("pm25").reset_index()
t = t[t["pm25"] >= checks.MIN_PLAUSIBLE]
prev_day = t["date"] - pd.Timedelta(days=1)
bad = [(l, d) in stuck_days or (l, p) in stuck_days
       for l, d, p in zip(t["location_id"], t["date"], prev_day)]
t = t[~np.array(bad)]
print(f"\nusable target station-days: {len(t):,}")

# Persistence baseline on the new target: tomorrow = today
t["date"] = pd.to_datetime(t["date"])
t = t.sort_values(["location_id", "date"])
g = t.groupby("location_id")
t["pred"] = g["pm25"].shift()
p = t[g["date"].diff() == pd.Timedelta(days=1)]
err = (p["pm25"] - p["pred"]).abs()
dry = p["date"].dt.month.isin([11, 12, 1, 2, 3, 4])
print(f"\npersistence on the new target ({len(p):,} station-days):")
print(f"  MAE {err.mean():.2f}   dry season (Nov-Apr) {err[dry].mean():.2f}   rainy (May-Oct) {err[~dry].mean():.2f}")
actual, pred = p["pm25"] > 37.5, p["pred"] > 37.5
print(f"  days > 37.5: {actual.sum():,}   recall {(actual & pred).sum()/actual.sum():.1%}"
      f"   false alarms {(pred & ~actual).sum()/pred.sum():.1%}")
print("  (old, on OpenAQ daily averages: MAE 4.05, recall 70.3%, false alarms 29.5%)")