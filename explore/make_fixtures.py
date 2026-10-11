import os, time, requests
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
H = {"X-API-Key": os.environ["OPENAQ_API_KEY"]}
OUT = Path("tests/fixtures"); OUT.mkdir(parents=True, exist_ok=True)

def hours(sensor_id, start, end):
    time.sleep(1.1)
    r = requests.get(f"https://api.openaq.org/v3/sensors/{sensor_id}/hours",
                     params={"datetime_from": start, "datetime_to": end, "limit": 1000},
                     headers=H, timeout=90)
    r.raise_for_status()
    return pd.DataFrame([{"time": x["period"]["datetimeFrom"]["local"], "pm25": x["value"]}
                         for x in r.json()["results"]])

samples = {
    "bang_sue_frozen_2023-09.csv":   (1304256, "2023-09-04T00:00:00+07:00", "2023-09-14T00:00:00+07:00"),
    "din_daeng_healthy_2023-09.csv": (1304357, "2023-09-04T00:00:00+07:00", "2023-09-14T00:00:00+07:00"),
}
for name, (sid, a, b) in samples.items():
    df = hours(sid, a, b)
    df.to_csv(OUT / name, index=False)
    print(f"{name}: {len(df)} hours, values {df['pm25'].min()}–{df['pm25'].max()}")