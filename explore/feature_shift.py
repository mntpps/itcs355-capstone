"""Which input looks different in validation than in training?"""
import sys

import pandas as pd

sys.path.insert(0, ".")
from src import config, features, split

cfg = config.load(strict=False)
df = features.build(pd.read_csv(cfg.targets_path), pd.read_csv(cfg.weather_daily_path),
                    pd.read_csv(cfg.stations_path))
df = df[df["y"].notna()].copy()
df["split"] = split.assign(df["date"]).values
df["delta"] = df["y"] - df["lag1"]
cols = ["y", "delta", "lag1", "mean7", "city_lag1", "city_n"] + features.WEATHER
print("average of each input, by split:")
print(df[df["split"] != "unused"].groupby("split")[cols].mean().T.round(2).to_string())

# Same calendar months, different years. The climate barely changes from one year to the
# next, so a big jump here means the forecast product changed, not the weather.
w = pd.read_csv(cfg.weather_daily_path, parse_dates=["date"])
w = w[w["date"].dt.month.isin([1, 2, 3])]
print("\nJan-Mar weather forecast averages, by year:")
print(w.groupby(w["date"].dt.year)[features.WEATHER].mean().round(2).to_string())