"""How wrong is a daily average built from only k of 24 hours?

Take complete days, delete one block of (24 - k) consecutive hours at a random position
(a realistic outage), and compare the average of what's left with the true 24-hour average.
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
h["date"] = h["time"].dt.date
h["hour"] = h["time"].dt.hour

# The daily cycle — this is why missing hours bias the average
cycle = h.groupby("hour")["pm25"].mean()
print("average PM2.5 by hour of day (Bangkok):")
print("  " + "  ".join(f"{hr:02d}h {v:.1f}" for hr, v in cycle.items()))
print(f"  highest {cycle.idxmax():02d}h ({cycle.max():.1f}), lowest {cycle.idxmin():02d}h ({cycle.min():.1f})")

grid = h.pivot_table(index=["location_id", "date"], columns="hour", values="pm25")
full = grid.dropna().to_numpy()                # complete days only, shape (days, 24)
true = full.mean(axis=1)
print(f"\ncomplete days used: {len(full):,}")

rng = np.random.default_rng(0)
cols = np.arange(24)
print("\nerror of the daily average when one block of hours is missing (µg/m³):")
print(f"  {'k':>3} {'median':>8} {'p95':>8} {'max':>8}")
for k in [12, 16, 18, 20, 22]:
    gap = 24 - k
    start = rng.integers(0, k + 1, size=len(full))
    hole = (cols >= start[:, None]) & (cols < start[:, None] + gap)
    est = np.nanmean(np.where(hole, np.nan, full), axis=1)
    err = np.abs(est - true)
    print(f"  {k:>3} {np.median(err):>8.2f} {np.percentile(err, 95):>8.2f} {err.max():>8.2f}")