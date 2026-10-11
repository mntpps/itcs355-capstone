import os, time, requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
H = {"X-API-Key": os.environ["OPENAQ_API_KEY"]}
BASE = "https://api.openaq.org/v3"

def get(path, **params):
    for attempt in range(5):
        time.sleep(1.1)
        r = requests.get(f"{BASE}{path}", params=params, headers=H, timeout=90)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(5 * 2 ** attempt); continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"gave up: {path}")

def hours(sensor_id, start, end):
    rows, page = [], 1
    while True:
        res = get(f"/sensors/{sensor_id}/hours", datetime_from=start,
                  datetime_to=end, limit=1000, page=page)["results"]
        rows += [(r["period"]["datetimeFrom"]["local"], r["value"]) for r in res]
        if len(res) < 1000:
            break
        page += 1
    s = pd.Series(dict(rows))
    s.index = pd.to_datetime(s.index, utc=True)
    return s.sort_index()

def runs(s):
    """Lengths of runs of identical consecutive hourly values.
    A run breaks when the value changes OR an hour is missing."""
    new_run = (s != s.shift()) | (s.index.to_series().diff() != pd.Timedelta(hours=1))
    run_id = new_run.cumsum()
    return s.groupby(run_id.values).size()

healthy = {"Din Dang": 1304357, "Lumpini park": 2076227, "Bang Phlat": 1304146,
           "Thai Meteorological Dept": 1304072, "Chatuchak Park": 2076226,
           "Khlong Toei": 1304071, "Odeon Circus": 1304435, "Nong Chok": 1304139}
frozen = {"Bang Sue (frozen Sep 2023)": (1304256, "2023-08-20", "2023-10-10"),
          "Mahaisawan (frozen Jun 2024)": (1304114, "2024-06-10", "2024-07-10")}

all_runs = []
print("HEALTHY stations, all of 2024")
for name, sid in healthy.items():
    s = hours(sid, "2024-01-01T00:00:00+07:00", "2025-01-01T00:00:00+07:00")
    r = runs(s); all_runs.append(r)
    print(f"  {name:<28} hours={len(s):>5}  longest run={r.max():>3}  "
          f"runs>=4h: {(r >= 4).sum():>3}  >=6h: {(r >= 6).sum():>3}  >=8h: {(r >= 8).sum():>3}")

h = pd.concat(all_runs)
print(f"\n  ALL HEALTHY: {len(h)} runs, 99th pct={h.quantile(0.99):.0f}h, "
      f"99.9th pct={h.quantile(0.999):.0f}h, max={h.max()}h")
for n in (3, 4, 5, 6, 8, 12):
    print(f"    runs of {n}h or more: {(h >= n).sum()}")

print("\nFROZEN episodes")
for name, (sid, a, b) in frozen.items():
    s = hours(sid, f"{a}T00:00:00+07:00", f"{b}T00:00:00+07:00")
    r = runs(s)
    print(f"  {name:<30} hours={len(s):>5}  longest run={r.max():>4}  "
          f"top runs={sorted(r.nlargest(5).tolist(), reverse=True)}")
