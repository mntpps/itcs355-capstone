"""How complete is the hourly history? Picks the 'enough hours for a daily value' rule."""
from pathlib import Path
import pandas as pd

TZ = "Asia/Bangkok"
stations = pd.read_csv("config/stations.csv")
months = [str(m) for m in pd.period_range("2024-01", "2026-10", freq="M")]

# 1. Did the backfill finish? Every station-month should have a file.
missing = [(s, m) for s in stations["location_id"] for m in months
           if not (Path("data/hourly") / str(s) / f"{m}.csv").exists()]
total = len(stations) * len(months)
print(f"files: {total - len(missing)} of {total} present")
for s, m in missing[:10]:
    print("  missing", s, m)

# 2. Load everything, in Bangkok time, up to yesterday (today is not complete)
parts = []
for f in Path("data/hourly").glob("*/*.csv"):
    d = pd.read_csv(f)
    if len(d):
        d["location_id"] = int(f.parent.name)
        parts.append(d)
h = pd.concat(parts, ignore_index=True)
h["time"] = pd.to_datetime(h["time_utc"], utc=True).dt.tz_convert(TZ)
today = pd.Timestamp.now(tz=TZ).normalize()
h = h[h["time"] < today]
h["date"] = h["time"].dt.date
print(f"hourly readings: {len(h):,}   empty values: {h['pm25'].isna().sum():,}")

# 3. Hours per station-day
n = h.groupby(["location_id", "date"]).size()
days = (today.date() - pd.Timestamp("2024-01-01").date()).days
possible = len(stations) * days
print(f"\nstation-days with at least 1 hour: {len(n):,} of {possible:,} possible ({len(n)/possible:.0%})")

print("\nhours per station-day (days that have any data):")
buckets = pd.cut(n, bins=[0, 5, 11, 17, 20, 23, 24],
                 labels=["1-5", "6-11", "12-17", "18-20", "21-23", "24"])
for label, count in buckets.value_counts().sort_index().items():
    print(f"  {label:>6} hours: {count:>7,}  ({count/len(n):.0%})")

print("\nif a day needs at least k hours to count:")
for k in [12, 16, 18, 20, 24]:
    ok = int((n >= k).sum())
    print(f"  k={k:>2}: {ok:>7,} usable station-days  ({ok/possible:.0%} of possible)")

# 4. Compare with the daily endpoint we used before
d = pd.concat(pd.read_csv(f"data/daily/{s}.csv") for s in stations["location_id"])
d = d[(d["date"] >= "2024-01-01") & d["pm25"].notna()]
print(f"\nfor comparison, daily endpoint station-days since 2024: {len(d):,}")