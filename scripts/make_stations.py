"""Build config/stations.csv — the frozen list of stations the service covers.

Rule: keep a station if it has daily data on at least half of the 1,379 days in
data/daily (2023-01-01 .. 2026-10-10). That drops the two parks with 344 and 192 days.
"""
from pathlib import Path
import pandas as pd

MIN_DAYS = 690

kept, dropped = [], []
for f in sorted(Path("data/daily").glob("*.csv")):
    d = pd.read_csv(f)
    if d.empty:
        continue
    row = {
        "location_id": int(d["location_id"].iloc[0]),
        "location": d["location"].iloc[0],
        "lat": d["lat"].iloc[0],
        "lon": d["lon"].iloc[0],
        "sensor_id": int(d["sensor_id"].mode()[0]),
    }
    days = int(d["pm25"].notna().sum())
    (kept if days >= MIN_DAYS else dropped).append((row, days))

Path("config").mkdir(exist_ok=True)
pd.DataFrame([r for r, _ in kept]).sort_values("location_id").to_csv("config/stations.csv", index=False)
print(f"kept {len(kept)} stations")
for r, days in dropped:
    print(f"dropped {r['location_id']} {r['location'][:40]} ({days} days)")
