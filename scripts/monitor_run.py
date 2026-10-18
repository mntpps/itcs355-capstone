"""Run the monitoring checks once; log one JSON line per finding.

    python scripts/monitor_run.py                      # live: last 48 h from OpenAQ + /ready
    python scripts/monitor_run.py --replay tests/fixtures/bang_sue_frozen_2023-09.csv

Findings go to stdout as JSON ("event": "finding"). In Azure, stdout lands in Log Analytics
and an alert rule emails on new findings, so this job needs no mail credentials.
Exit code 0 = the MONITOR ran (with or without findings); non-zero = the monitor itself broke.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from src import checks, ingest, monitor  # noqa: E402

TZ = "Asia/Bangkok"
LOOKBACK = pd.Timedelta(hours=48)


def emit(event: str, **fields) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "event": event, **fields}), flush=True)


def fetch_recent(now: pd.Timestamp):
    stations = pd.read_csv(REPO_ROOT / "config" / "stations.csv")
    start = f"{now - LOOKBACK:%Y-%m-%dT%H:%M:%S}+07:00"
    end = f"{now:%Y-%m-%dT%H:%M:%S}+07:00"
    parts = []
    for i, s in enumerate(stations.itertuples(), 1):
        print(f"  fetching {i}/{len(stations)} station {s.location_id}", file=sys.stderr, flush=True)
        df = ingest.fetch_hours(s.sensor_id, start, end)
        if df.empty:
            continue
        parts.append(pd.DataFrame({
            "location_id": str(s.location_id),
            "time": pd.to_datetime(df["time_utc"], utc=True).dt.tz_convert(TZ),
            "pm25": df["pm25"].astype(float)}))
    hourly = (pd.concat(parts, ignore_index=True) if parts
              else pd.DataFrame(columns=["location_id", "time", "pm25"]))
    return hourly, len(stations)


def service_findings(url: str) -> list:
    try:
        r = requests.get(f"{url.rstrip('/')}/ready", timeout=120)   # allows a cold start
    except requests.RequestException as exc:
        return [monitor.Finding("service_unreachable", "critical",
                                f"{url}/ready unreachable: {type(exc).__name__}")]
    if r.status_code != 200:
        return [monitor.Finding("service_not_ready", "critical",
                                f"/ready returned {r.status_code}", {"body": r.text[:300]})]
    findings = monitor.stale_data(r.json()["data_age_days"])
    try:
        p = requests.get(f"{url.rstrip('/')}/performance", timeout=60)
        if p.status_code == 200:
            perf = p.json()
            emit("model_performance", rows=perf.get("rows", 0), window=perf.get("window"),
                 mae_model=perf.get("mae_model"), mae_persistence=perf.get("mae_persistence"),
                 model_version=perf.get("model_version"), data_version=perf.get("data_version"))
            findings += monitor.from_performance(perf)
    except requests.RequestException:
        pass            # /ready answered a moment ago; a real outage is reported as service_unreachable
    return findings


def replay(path: str):
    """A recorded incident, replayed up to its last frozen hour as if that were now."""
    df = pd.read_csv(path)
    s = pd.Series(df["pm25"].values, index=pd.to_datetime(df["time"], utc=True)).sort_index()
    frozen = checks.stuck_mask(s)
    if not frozen.any():
        sys.exit(f"{path}: no frozen period to replay")
    at = s.index[frozen][-1]
    hourly = pd.DataFrame({"location_id": Path(path).stem, "time": s.index, "pm25": s.values})
    return hourly[hourly["time"] <= at], at


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", help="CSV (time, pm25) of a recorded incident to replay")
    args = ap.parse_args()
    t0 = time.perf_counter()

    if args.replay:
        mode = "replay"
        hourly, now = replay(args.replay)
        findings = monitor.stuck_sensors(hourly, now)
    else:
        mode = "live"
        now = pd.Timestamp.now(tz=TZ).floor("h")
        hourly, expected = fetch_recent(now)
        findings = monitor.feed_outage(hourly, now, expected) + monitor.stuck_sensors(hourly, now)
        if os.environ.get("SERVICE_URL"):
            findings += service_findings(os.environ["SERVICE_URL"])

    for f in findings:
        emit("finding", mode=mode, check=f.check, severity=f.severity,
             message=f.message, details=f.details)
    emit("monitor_run", mode=mode, findings=len(findings), as_of=str(now),
         stations_reporting=int(hourly["location_id"].nunique()), hours=len(hourly),
         seconds=round(time.perf_counter() - t0, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())