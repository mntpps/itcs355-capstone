"""Daily refresh of the serving inputs: targets.csv and weather_daily.csv.

    python -m src.refresh --dry-run      # local: read data/, write build/refresh/, upload nothing
    python -m src.refresh                # cloud job: BLOB_URI set, publishes

Fetches the last --days days from OpenAQ and the weather API, turns them into
daily rows with the same code as training (ingest.hourly_series,
target.daily_target, weather.daily_features), and replaces those days in the
current files. Publishing uploads the new files to the DVC remote by md5, then
writes serving/current.json. The manifest is written last, so a failed run never
leaves the service pointing at half-published data.

Fails closed: if too many stations fail, or a file comes out with different
columns or fewer rows, nothing is published and the exit code is 1.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src import config, data_source, ingest, target, weather

TZ = ingest.TZ
NAMES = ("targets.csv", "weather_daily.csv")
MAX_FAILED = 0.2            # more than 20% of stations failing = don't publish


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def day_window(days: int, now: pd.Timestamp | None = None) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end): `days` whole Bangkok days ending at today 00:00 (today is not finished)."""
    now = now if now is not None else pd.Timestamp.now(tz=TZ)
    end = now.tz_convert(TZ).normalize()
    return end - pd.Timedelta(days=days), end


def _ymd(col: pd.Series) -> pd.Series:
    return pd.to_datetime(col.astype(str)).dt.strftime("%Y-%m-%d")


def in_window(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = df.copy()
    df["date"] = _ymd(df["date"])
    return df[(df["date"] >= f"{start:%Y-%m-%d}") & (df["date"] < f"{end:%Y-%m-%d}")]


def replace_window(old: pd.DataFrame, new: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Rows in `new` replace the same keys in `old`; every other old row is kept."""
    old = old.copy()
    old["date"] = _ymd(old["date"])
    if new.empty:
        return old
    new = new[list(old.columns)]
    hit = old.merge(new[keys].drop_duplicates(), on=keys, how="left", indicator=True)["_merge"]
    keep = old[(hit == "left_only").to_numpy()]
    out = pd.concat([keep, new], ignore_index=True)
    return out.sort_values(keys).reset_index(drop=True)


def recent_targets(stations: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                   fetch=ingest.fetch_hours) -> tuple[pd.DataFrame, list]:
    parts, failed = [], []
    for i, s in enumerate(stations.itertuples(), 1):
        try:
            df = fetch(s.sensor_id, start.isoformat(), end.isoformat())
        except Exception as e:  # noqa: BLE001 - one bad station must not stop the rest
            failed.append(int(s.location_id))
            log(f"[{i}/{len(stations)}] {s.location_id}: FAILED {type(e).__name__}: {e}")
            continue
        log(f"[{i}/{len(stations)}] {s.location_id}: {len(df)} hours")
        if df.empty:
            continue                    # silent station: the monitor reports it as an outage
        t = target.daily_target(ingest.hourly_series(df))
        t.insert(0, "location_id", s.location_id)
        t = in_window(t, start, end)
        if not t.empty:                 # skip empty frames (pandas deprecates concatenating them)
            parts.append(t)
    cols = ["location_id", "date"]
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)
    return out, failed


def recent_weather(start: pd.Timestamp, end: pd.Timestamp, fetch=weather.fetch_hourly) -> pd.DataFrame:
    daily = weather.daily_features(fetch(f"{start:%Y-%m-%d}", f"{end:%Y-%m-%d}"))
    if "date" not in daily.columns:
        daily = daily.reset_index()
    return in_window(daily, start, end)


def check(name: str, old: pd.DataFrame, new: pd.DataFrame) -> list[str]:
    problems = []
    if list(new.columns) != list(old.columns):
        problems.append(f"{name}: columns changed {list(old.columns)} -> {list(new.columns)}")
    if len(new) < len(old):
        problems.append(f"{name}: fewer rows than before ({len(old):,} -> {len(new):,})")
    return problems


def summary(name: str, old: pd.DataFrame, new: pd.DataFrame) -> str:
    return (f"{name}: {len(old):,} -> {len(new):,} rows, last date "
            f"{_ymd(old['date']).max()} -> {new['date'].max()}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10, help="whole days to re-fetch (default 10)")
    ap.add_argument("--dry-run", action="store_true", help="write to --out, upload nothing")
    ap.add_argument("--out", type=Path, default=Path("build/refresh"))
    args = ap.parse_args(argv)

    blob_uri = os.environ.get("BLOB_URI")
    if not args.dry_run and not blob_uri:
        log("BLOB_URI is not set: use --dry-run locally")
        return 2

    cfg = config.load()
    paths = {"targets.csv": cfg.targets_path, "weather_daily.csv": cfg.weather_daily_path}
    if blob_uri:
        base, base_from = data_source.serving_versions(NAMES, blob_uri)
        for n in NAMES:
            data_source.ensure(paths[n], blob_uri, want=base[n])
    else:
        base, base_from = {n: data_source.file_md5(paths[n]) for n in NAMES}, "local"
    old = {n: pd.read_csv(paths[n]) for n in NAMES}

    start, end = day_window(args.days)
    log(f"re-fetching {start:%Y-%m-%d} .. {end - pd.Timedelta(days=1):%Y-%m-%d} "
        f"(base: {base_from})")
    stations = pd.read_csv(ingest.STATIONS)
    t_new, failed = recent_targets(stations, start, end)
    new = {
        "targets.csv": replace_window(old["targets.csv"], t_new, ["location_id", "date"]),
        "weather_daily.csv": replace_window(old["weather_daily.csv"],
                                            recent_weather(start, end), ["date"]),
    }

    problems = [p for n in NAMES for p in check(n, old[n], new[n])]
    if len(failed) > MAX_FAILED * len(stations):
        problems.append(f"{len(failed)}/{len(stations)} stations failed")
    for n in NAMES:
        log(summary(n, old[n], new[n]))
    if problems:
        for p in problems:
            log(f"NOT PUBLISHED: {p}")
        return 1

    out_dir = args.out if args.dry_run else Path(tempfile.mkdtemp())
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for n in NAMES:
        p = out_dir / n
        new[n].to_csv(p, index=False)
        files[n] = {"md5": data_source.file_md5(p), "rows": len(new[n]),
                    "last_date": str(new[n]["date"].max())}
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": [f"{start:%Y-%m-%d}", f"{end:%Y-%m-%d}"],
        "base": {"from": base_from, **base},
        "files": files,
        "stations": len(stations),
        "stations_failed": failed,
    }

    if all(files[n]["md5"] == base[n] for n in NAMES):
        log("no change: nothing to publish")
        return 0
    if args.dry_run:
        (out_dir / "current.json").write_text(json.dumps(manifest, indent=2))
        log(f"dry run: wrote {out_dir}/ (nothing uploaded)")
    else:
        for n in NAMES:
            data_source._upload(blob_uri, data_source.dvc_key(files[n]["md5"]),
                                (out_dir / n).read_bytes(), overwrite=False)
        data_source._upload(blob_uri, data_source.MANIFEST_KEY,
                            json.dumps(manifest, indent=2).encode(), overwrite=True)
        log(f"published {data_source.MANIFEST_KEY}")
    print(json.dumps({"event": "refresh", **manifest}))     # one JSON line for Log Analytics
    return 0


if __name__ == "__main__":
    sys.exit(main())