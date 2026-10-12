"""Ingest hourly PM2.5 from OpenAQ and build the daily target table.

    python -m src.ingest backfill --start 2024-01   # history, resumable
    python -m src.ingest build                      # hourly files -> data/targets.csv
    python -m src.ingest update                     # nightly: refresh recent months, then build

Hourly files: data/hourly/<location_id>/<YYYY-MM>.csv  (Bangkok calendar months)
"""
import argparse
from pathlib import Path

import pandas as pd

from src import openaq, target

STATIONS = Path("config/stations.csv")
HOURLY = Path("data/hourly")
TARGETS = Path("data/targets.csv")
TZ = "Asia/Bangkok"


# ---------- download ----------

def fetch_hours(sensor_id: int, start: str, end: str) -> pd.DataFrame:
    """Every hourly reading between start and end (ISO strings with +07:00), all pages."""
    rows, page = [], 1
    while True:
        body = openaq.get(f"/sensors/{sensor_id}/hours",
                          {"datetime_from": start, "datetime_to": end,
                           "limit": 1000, "page": page})
        rows += [{"time_utc": x["period"]["datetimeFrom"]["utc"], "pm25": x["value"]}
                 for x in body["results"]]
        if len(body["results"]) < 1000:
            return pd.DataFrame(rows, columns=["time_utc", "pm25"])
        page += 1


def fetch_month(sensor_id: int, month: str) -> pd.DataFrame:
    p = pd.Period(month, "M")
    start = f"{p.start_time:%Y-%m-%d}T00:00:00+07:00"
    end = f"{(p + 1).start_time:%Y-%m-%d}T00:00:00+07:00"
    df = fetch_hours(sensor_id, start, end)
    local = pd.to_datetime(df["time_utc"], utc=True).dt.tz_convert(TZ)
    df = df[local.dt.strftime("%Y-%m") == month]
    return df.drop_duplicates("time_utc").sort_values("time_utc")


def backfill(start: str, end: str, force: frozenset = frozenset()) -> None:
    """Download station-months. Skips saved months unless current or listed in `force`."""
    stations = pd.read_csv(STATIONS)
    current = pd.Timestamp.now(tz=TZ).strftime("%Y-%m")
    months = [str(m) for m in pd.period_range(start, end, freq="M")]
    todo = [(s, m) for s in stations.itertuples() for m in months]
    for i, (s, m) in enumerate(todo, 1):
        out = HOURLY / str(s.location_id) / f"{m}.csv"
        if out.exists() and m != current and m not in force:
            continue
        df = fetch_month(s.sensor_id, m)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        df.to_csv(tmp, index=False)
        tmp.replace(out)                  # a crash never leaves a half-written file
        print(f"[{i}/{len(todo)}] {s.location_id} {m}: {len(df)} hours")


# ---------- build ----------

def load_hourly(location_id: int) -> pd.Series:
    files = sorted((HOURLY / str(location_id)).glob("*.csv"))
    if not files:
        return pd.Series(dtype=float)
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df = df.drop_duplicates("time_utc")
    return pd.Series(df["pm25"].values,
                     index=pd.DatetimeIndex(pd.to_datetime(df["time_utc"], utc=True)))


def build_targets() -> pd.DataFrame:
    stations = pd.read_csv(STATIONS)
    today = pd.Timestamp.now(tz=TZ).date()
    parts = []
    for s in stations.itertuples():
        hourly = load_hourly(s.location_id)
        if hourly.empty:
            continue
        t = target.daily_target(hourly)
        t.insert(0, "location_id", s.location_id)
        parts.append(t)
    out = pd.concat(parts, ignore_index=True)
    out = out[out["date"] < today]        # today is not finished
    out = out.sort_values(["location_id", "date"])
    TARGETS.parent.mkdir(parents=True, exist_ok=True)
    tmp = TARGETS.with_suffix(".tmp")
    out.to_csv(tmp, index=False)
    tmp.replace(TARGETS)

    print(f"wrote {TARGETS}: {len(out):,} station-days, {out['location_id'].nunique()} stations, "
          f"{out['date'].min()} .. {out['date'].max()}")
    for src, n in out["source"].value_counts().items():
        print(f"  from {src}: {n:,}")
    return out


def update() -> None:
    """Nightly: refresh this month and last month (last month may have been saved
    before it ended), then rebuild the target table."""
    current = pd.Period(pd.Timestamp.now(tz=TZ).strftime("%Y-%m"), "M")
    previous = str(current - 1)
    backfill(previous, str(current), force=frozenset({previous}))
    build_targets()


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--start", default="2024-01")
    b.add_argument("--end", default=pd.Timestamp.now(tz=TZ).strftime("%Y-%m"))
    sub.add_parser("build")
    sub.add_parser("update")
    args = ap.parse_args()
    if args.cmd == "backfill":
        backfill(args.start, args.end)
    elif args.cmd == "build":
        build_targets()
    elif args.cmd == "update":
        update()


if __name__ == "__main__":
    main()