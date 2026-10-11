import os, time, requests
from dotenv import load_dotenv

load_dotenv()
H = {"X-API-Key": os.environ["OPENAQ_API_KEY"]}
BASE = "https://api.openaq.org/v3"

def days(sensor_id, date_from):
    time.sleep(1.1)
    r = requests.get(f"{BASE}/sensors/{sensor_id}/days",
                     params={"date_from": date_from, "date_to": "2026-10-11", "limit": 100},
                     headers=H, timeout=60)
    r.raise_for_status()
    return [(d["period"]["datetimeFrom"]["local"][:10], d["value"],
             d["coverage"]["percentCoverage"], d["summary"]["sd"])
            for d in r.json()["results"]]

pairs = {"Lumpini park": (2076227, 16312736),
         "Din Dang": (1304357, 16312664),
         "Bang Phlat": (1304146, 16312587)}

for name, (old, new) in pairs.items():
    print(f"\n== {name}")
    for label, sid in (("old", old), ("new", new)):
        rows = days(sid, "2026-09-28")
        print(f"  {label} sensor {sid}: {len(rows)} days since 2026-09-28")
        for date, value, cov, sd in rows[-5:]:
            print(f"     {date}  pm25={value:6.1f}  coverage={cov:5.1f}%  sd={sd}")