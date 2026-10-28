"""How often would a 'station disagrees with its neighbours' rule have fired in the history?

    python explore/neighbour_thresholds.py

Reads data/hourly/<location_id>/<YYYY-MM>.csv (all stations) and config/stations.csv.
For each candidate rule it counts EPISODES: runs of consecutive hours where a station's
value is far from the median of its neighbours. Few episodes per month across all
stations = a rule that does not cry wolf; the evidence behind the thresholds in
src/checks.py (like Finding 4 for the stuck-sensor rule).
"""
from __future__ import annotations

import itertools
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_hourly() -> pd.DataFrame:
    parts = []
    for d in sorted((ROOT / "data" / "hourly").iterdir()):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.csv")):
            df = pd.read_csv(f)
            if df.empty:
                continue
            df["location_id"] = d.name
            parts.append(df)
    hourly = pd.concat(parts, ignore_index=True)
    hourly["time"] = pd.to_datetime(hourly["time_utc"], utc=True)
    return hourly.pivot_table(index="time", columns="location_id", values="pm25", aggfunc="mean")


def distances_km(stations: pd.DataFrame) -> pd.DataFrame:
    lat = np.radians(stations["lat"].to_numpy(float))
    lon = np.radians(stations["lon"].to_numpy(float))
    dy = (lat[:, None] - lat[None, :]) * 6371
    dx = (lon[:, None] - lon[None, :]) * 6371 * np.cos((lat[:, None] + lat[None, :]) / 2)
    ids = stations["location_id"].astype(str)
    return pd.DataFrame(np.hypot(dx, dy), index=ids, columns=ids)


def run_lengths(mask: np.ndarray) -> list[int]:
    """Lengths of the runs of True in a boolean array."""
    out, n = [], 0
    for v in mask:
        if v:
            n += 1
        elif n:
            out.append(n)
            n = 0
    if n:
        out.append(n)
    return out


def main() -> int:
    stations = pd.read_csv(ROOT / "config" / "stations.csv")
    wide = load_hourly()
    wide = wide.reindex(pd.date_range(wide.index.min(), wide.index.max(), freq="h"))
    dist = distances_km(stations)
    ids = [i for i in dist.index if i in wide.columns]
    months = len(wide) / (24 * 30.4)
    print(f"{len(ids)} stations, {len(wide):,} hours ({wide.index.min():%Y-%m} .. "
          f"{wide.index.max():%Y-%m}, {months:.0f} months)\n")

    for radius in (10, 15):
        have = sum((dist.loc[i, ids] <= radius).sum() - 1 >= 3 for i in ids)
        print(f"stations with >= 3 neighbours within {radius} km: {have}/{len(ids)}")
    print()

    radius, min_nb = 10, 3
    values = wide[ids].to_numpy(float)
    med = np.full_like(values, np.nan)
    cnt = np.zeros_like(values)
    for k, i in enumerate(ids):
        nb = [ids.index(j) for j in ids if j != i and dist.loc[i, j] <= radius]
        if len(nb) >= min_nb:
            block = values[:, nb]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)   # hours with no neighbour -> NaN
                med[:, k] = np.nanmedian(block, axis=1)
            cnt[:, k] = (~np.isnan(block)).sum(axis=1)

    rows = []
    for (low, high), gap, hours in itertools.product(
            [(0.5, 2.0), (0.4, 2.5), (1 / 3, 3.0)], [15.0, 25.0], [6, 12, 24]):
        with np.errstate(invalid="ignore"):
            off = ((cnt >= min_nb) & ~np.isnan(values) & ~np.isnan(med)
                   & ((values < med * low) | (values > med * high))
                   & (np.abs(values - med) >= gap))
        episodes, flagged = 0, set()
        for k in range(len(ids)):
            long_runs = [n for n in run_lengths(off[:, k]) if n >= hours]
            episodes += len(long_runs)
            if long_runs:
                flagged.add(ids[k])
        rows.append({"low": round(low, 2), "high": high, "min_gap": gap, "hours": hours,
                     "episodes": episodes, "per_month": round(episodes / months, 2),
                     "stations_ever": len(flagged)})
    table = pd.DataFrame(rows)
    print(f"Rule: value < low x median or > high x median of neighbours within {radius} km "
          f"(>= {min_nb} reporting),\n      AND |value - median| >= min_gap, for >= hours in a row\n")
    print(table.to_string(index=False))

    # the episodes of the middle rule, to look at by eye
    low, high, gap, hours = 0.5, 2.0, 15.0, 12
    with np.errstate(invalid="ignore"):
        off = ((cnt >= min_nb) & ~np.isnan(values) & ~np.isnan(med)
               & ((values < med * low) | (values > med * high)) & (np.abs(values - med) >= gap))
    print(f"\nEpisodes of the rule low={low}, high={high}, min_gap={gap}, hours={hours} (longest first):")
    found = []
    for k, i in enumerate(ids):
        start = None
        for t in range(len(off) + 1):
            on = t < len(off) and off[t, k]
            if on and start is None:
                start = t
            elif not on and start is not None:
                if t - start >= hours:
                    found.append((t - start, i, wide.index[start], np.nanmean(values[start:t, k]),
                                  np.nanmean(med[start:t, k])))
                start = None
    for n, i, t0, v, m in sorted(found, reverse=True)[:15]:
        print(f"  station {i:>8}  from {t0:%Y-%m-%d %H:00} UTC  {n:4d} h  "
              f"station {v:6.1f}  neighbours {m:6.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())