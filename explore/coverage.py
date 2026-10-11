import os, time, requests, csv
from dotenv import load_dotenv

load_dotenv()
H = {"X-API-Key": os.environ["OPENAQ_API_KEY"]}
BASE = "https://api.openaq.org/v3"

def get(path, **params):
    time.sleep(1.1)                      # stay under 60 requests/minute
    r = requests.get(f"{BASE}{path}", params=params, headers=H, timeout=60)
    r.raise_for_status()
    return r.json()

locs = get("/locations", bbox="100.33,13.49,100.94,13.96",
           parameters_id=2, monitor="true", limit=1000)["results"]
live = [l for l in locs
        if l["provider"]["name"] == "Air4Thai"
        and ((l.get("datetimeLast") or {}).get("local") or "") >= "2026-10"]
print(len(live), "live Air4Thai monitors")

# print one daily record once, so we can see the real field names
shown = False
out = []
for loc in live:
    for s in loc["sensors"]:
        if s["parameter"]["name"] != "pm25":
            continue
        days = get(f"/sensors/{s['id']}/days",
                   date_from="2023-01-01", date_to="2026-10-10", limit=1000)
        res = days["results"]
        if not shown and res:
            print("example day record:", res[0]); shown = True
        found = days["meta"]["found"]
        out.append({"location": loc["name"], "loc_id": loc["id"],
                    "sensor_id": s["id"], "days_found": found})
        print(f"{(loc['name'] or '')[:40]:<40} sensor={s['id']:<9} days={found}")

with open("data/coverage.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=out[0].keys()); w.writeheader(); w.writerows(out)