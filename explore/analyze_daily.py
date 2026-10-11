import pandas as pd
from pathlib import Path

df = pd.concat(pd.read_csv(p) for p in Path("data/daily").glob("*.csv"))
df["date"] = pd.to_datetime(df["date"])
n_stations = df["location_id"].nunique()
print(f"{len(df):,} rows, {n_stations} stations, "
      f"{df['date'].min().date()} to {df['date'].max().date()}")

# 1. shared gaps: how many stations reported on each date?
all_dates = pd.date_range("2023-01-01", "2026-10-10")
per_day = df.groupby("date")["location_id"].nunique().reindex(all_dates, fill_value=0)
bad = per_day[per_day < n_stations * 0.5]
print(f"\n1. Dates where fewer than half the stations reported: {len(bad)}")
runs = (bad.index.to_series().diff().dt.days != 1).cumsum()
for _, g in bad.groupby(runs.values):
    print(f"   {g.index[0].date()} to {g.index[-1].date()}  ({len(g)} days, "
          f"min {g.min()} stations)")

# 2. real stuck days: every reading that day was identical
stuck = df[(df["min"] == df["max"]) & (df["coverage_pct"] >= 50)]
print(f"\n2. Days where min == max (all readings identical): {len(stuck)}")
print(stuck.groupby("location")["date"].count().sort_values(ascending=False).head(10))

# 3. seasonality: average PM2.5 by month, across all stations
monthly = df.groupby(df["date"].dt.to_period("M"))["pm25"].mean().round(1)
print("\n3. Monthly average PM2.5 (µg/m³):")
print(monthly.to_string())

for name in ["Bang Sue District Office, Bangkok", "Mahaisawan Intersection"]:
    s = stuck[stuck["location"] == name].sort_values("date")
    print(f"\n{name}:")
    print(s[["date", "pm25", "min", "max", "coverage_pct"]].to_string(index=False))

print("\nStuck values:", stuck["pm25"].value_counts().to_dict())