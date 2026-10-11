import os, time, requests
from dotenv import load_dotenv

load_dotenv()
H = {"X-API-Key": os.environ["OPENAQ_API_KEY"]}
BASE = "https://api.openaq.org/v3"

params = {"bbox": "100.33,13.49,100.94,13.96",
          "parameters_id": 2,        # PM2.5 (check the output says pm25)
          "limit": 1000}
r = requests.get(f"{BASE}/locations", params=params, headers=H, timeout=60)
r.raise_for_status()
locs = r.json()["results"]

rows = []
for loc in locs:
    pm25 = [s for s in loc["sensors"] if s["parameter"]["name"] == "pm25"]
    if not pm25:
        continue
    last = (loc.get("datetimeLast") or {}).get("local") or ""
    first = (loc.get("datetimeFirst") or {}).get("local") or ""
    rows.append((last, first, loc["id"], pm25[0]["id"], loc["isMonitor"],
                 loc["provider"]["name"], loc["name"],
                 [l["name"] for l in loc.get("licenses") or []]))

rows.sort(reverse=True)   # most recently active first
print(f"{len(rows)} PM2.5 locations")
for last, first, lid, sid, mon, prov, name, lic in rows:
    print(f"{last[:10]:<11} {first[:10]:<11} loc={lid:<8} sensor={sid:<9} "
          f"monitor={mon!s:<5} {prov[:20]:<20} {name[:40]:<40} {lic}")