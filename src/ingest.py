"""Ingest hourly PM2.5 from OpenAQ.

    python -m src.ingest backfill --start 2024-01      # history, resumable

Files: data/hourly/<location_id>/<YYYY-MM>.csv  (months are Bangkok calendar months)
"""
import argparse
from pathlib import Path

import pandas as pd

from src import openaq

STATIONS = Path("config/stations.csv")
HOURLY = Path("data/hourly")
TZ = "Asia/Bangkok"


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
    # Keep exactly this Bangkok month, whatever the API does at the edges
    local = pd.to_datetime(df["time_utc"], utc=True).dt.tz_convert(TZ)
    df = df[local.dt.strftime("%Y-%m") == month]
    return df.drop_duplicates("time_utc").sort_values("time_utc")


def backfill(start: str, end: str) -> None:
    stations = pd.read_csv(STATIONS)
    current = pd.Timestamp.now(tz=TZ).strftime("%Y-%m")
    months = [str(m) for m in pd.period_range(start, end, freq="M")]
    todo = [(s, m) for s in stations.itertuples() for m in months]
    for i, (s, m) in enumerate(todo, 1):
        out = HOURLY / str(s.location_id) / f"{m}.csv"
        if out.exists() and m != current:
            continue
        df = fetch_month(s.sensor_id, m)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        df.to_csv(tmp, index=False)
        tmp.replace(out)                  # a crash never leaves a half-written file
        print(f"[{i}/{len(todo)}] {s.location_id} {m}: {len(df)} hours")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--start", default="2024-01")
    b.add_argument("--end", default=pd.Timestamp.now(tz=TZ).strftime("%Y-%m"))
    args = ap.parse_args()
    if args.cmd == "backfill":
        backfill(args.start, args.end)


if __name__ == "__main__":
    main()