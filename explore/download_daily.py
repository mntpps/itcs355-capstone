import os, time, csv, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
H = {"X-API-Key": os.environ["OPENAQ_API_KEY"]}
BASE = "https://api.openaq.org/v3"
OUT = Path("data/daily"); OUT.mkdir(parents=True, exist_ok=True)
YEARS = [("2023-01-01", "2023-12-31"), ("2024-01-01", "2024-12-31"),
         ("2025-01-01", "2025-12-31"), ("2026-01-01", "2026-10-11")]

def get(path, **params):
    for attempt in range(5):
        time.sleep(1.1)
        r = requests.get(f"{BASE}{path}", params=params, headers=H, timeout=90)
        if r.status_code in (429, 500, 502, 503, 504):
            wait = 5 * 2 ** attempt                      # 5, 10, 20, 40, 80 s
            print(f"   {r.status_code} on {path} — retrying in {wait}s")
            time.sleep(wait); continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"gave up after 5 tries: {path} {params}")

def all_days(sensor_id, date_from, date_to):
    rows, page = [], 1
    while True:
        res = get(f"/sensors/{sensor_id}/days", date_from=date_from,
                  date_to=date_to, limit=1000, page=page)["results"]
        rows += res
        if len(res) < 1000:
            return rows
        page += 1

def active_sensor(loc):
    """The PM2.5 sensor that has reported this month (cheap: 1 row per sensor)."""
    for s in loc["sensors"]:
        if s["parameter"]["name"] == "pm25":
            recent = get(f"/sensors/{s['id']}/days", date_from="2026-10-01",
                         date_to="2026-10-11", limit=1)["results"]
            if recent:
                return s["id"]
    return None

locs = get("/locations", bbox="100.33,13.49,100.94,13.96",
           parameters_id=2, monitor="true", limit=1000)["results"]
live = [l for l in locs if l["provider"]["name"] == "Air4Thai"
        and ((l.get("datetimeLast") or {}).get("local") or "") >= "2026-10"]

for loc in live:
    path = OUT / f"{loc['id']}.csv"
    if path.exists():
        continue                                         # already done — resume
    sid = active_sensor(loc)
    if sid is None:
        print(f"{(loc['name'] or '')[:40]:<40} no active PM2.5 sensor — skipped")
        continue
    rows = []
    for a, b in YEARS:
        rows += all_days(sid, a, b)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["location_id", "location", "lat", "lon", "sensor_id",
                    "date", "pm25", "coverage_pct", "sd", "min", "max"])
        for d in rows:
            w.writerow([loc["id"], loc["name"], loc["coordinates"]["latitude"],
                        loc["coordinates"]["longitude"], sid,
                        d["period"]["datetimeFrom"]["local"][:10], d["value"],
                        d["coverage"]["percentCoverage"], d["summary"]["sd"],
                        d["summary"]["min"], d["summary"]["max"]])
    tmp.rename(path)                                     # only complete files count
    print(f"{(loc['name'] or '')[:40]:<40} sensor={sid:<9} days={len(rows)}")