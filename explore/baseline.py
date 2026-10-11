import pandas as pd
from pathlib import Path

df = pd.concat(pd.read_csv(p) for p in Path("data/daily").glob("*.csv"))
df["date"] = pd.to_datetime(df["date"])

# --- your data rules ---------------------------------------------------------
drop_stations = ["Wacchirabenchatat Park"]
df = df[~df["location"].str.startswith("Public Park to commemorate")]
df = df[~df["location"].isin(drop_stations)]
before = len(df)
df = df[~((df["min"] == df["max"]) & (df["coverage_pct"] >= 50))]   # frozen days
df = df[df["pm25"] >= 1]                                             # zero / broken
df = df[df["date"] >= "2024-01-01"]                                  # training window
print(f"rows after cleaning (2024+): {len(df):,}  (frozen/zero removed: {before - len(df):,} incl. 2023)")

# --- persistence: predict D from D-1, same station ---------------------------
df = df.sort_values(["location_id", "date"])
prev = df.groupby("location_id")[["date", "pm25"]].shift(1)
ok = (df["date"] - prev["date"]) == pd.Timedelta(days=1)            # only true consecutive days
p = df[ok].assign(pred=prev.loc[ok, "pm25"])
p["err"] = (p["pred"] - p["pm25"]).abs()

print(f"\nPersistence on {len(p):,} station-days")
print(f"  MAE  = {p['err'].mean():.2f} µg/m³")
print(f"  RMSE = {((p['pred'] - p['pm25'])**2).mean() ** 0.5:.2f} µg/m³")

season = p["date"].dt.month.map(lambda m: "dry (Nov-Apr)" if m in (11,12,1,2,3,4) else "rainy (May-Oct)")
print("\nMAE by season:")
print(p.groupby(season)["err"].mean().round(2).to_string())

# --- the days users care about: will tomorrow be unhealthy? ------------------
T = 37.5   # threshold for "unhealthy" — check the current Thai PCD bands before using
hi_actual, hi_pred = p["pm25"] > T, p["pred"] > T
print(f"\nDays above {T}: {hi_actual.sum():,}")
print(f"  caught by persistence (recall):    {(hi_actual & hi_pred).sum() / hi_actual.sum():.1%}")
print(f"  false alarms among its warnings:   {(~hi_actual & hi_pred).sum() / hi_pred.sum():.1%}")
print(f"  missed onsets (yesterday fine, today bad): "
      f"{(hi_actual & ~hi_pred).sum():,}")