"""What the project has ACTUALLY cost, from Azure Cost Management (not an estimate).

    python scripts/cost_actual.py                       # from 2026-10-01 to today
    python scripts/cost_actual.py --from 2026-09-01

Prints cost per Azure service and per day for this resource group, and writes
reports/cost-actual.json (the raw rows, so the report's numbers can be re-checked).
Cost Management data lags by up to ~24 h, so the last day is usually incomplete.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from src import config  # noqa: E402

API = "api-version=2023-03-01"


def query(url: str, body: dict) -> list[dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f)
    try:
        r = subprocess.run([shutil.which("az"), "rest", "--method", "post", "--url", url,
                            "--body", f"@{f.name}", "-o", "json"], capture_output=True, text=True)
    finally:
        os.unlink(f.name)
    if r.returncode != 0:
        sys.exit(f"Cost Management query failed:\n{r.stderr}")
    p = json.loads(r.stdout)["properties"]
    cols = [c["name"] for c in p["columns"]]
    return [dict(zip(cols, row)) for row in p["rows"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2026-10-01")
    ap.add_argument("--to", dest="end", default=f"{date.today():%Y-%m-%d}")
    args = ap.parse_args()

    config.load()
    rg, sub = os.environ["PROJECT_ID"], os.environ["AZURE_SUBSCRIPTION_ID"]
    url = (f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
           f"/providers/Microsoft.CostManagement/query?{API}")
    period = {"from": f"{args.start}T00:00:00Z", "to": f"{args.end}T23:59:59Z"}
    cost = {"totalCost": {"name": "Cost", "function": "Sum"}}

    by_service = query(url, {"type": "ActualCost", "timeframe": "Custom", "timePeriod": period,
                             "dataset": {"granularity": "None", "aggregation": cost,
                                         "grouping": [{"type": "Dimension", "name": "ServiceName"}]}})
    by_day = query(url, {"type": "ActualCost", "timeframe": "Custom", "timePeriod": period,
                         "dataset": {"granularity": "Daily", "aggregation": cost}})

    cur = (by_service or by_day or [{"Currency": "?"}])[0].get("Currency", "?")
    total = sum(r["Cost"] for r in by_service)
    print(f"Actual cost, resource group {rg}, {args.start} .. {args.end}: {total:.2f} {cur}\n")
    print(f"{'service':<34}{'cost':>10}")
    for r in sorted(by_service, key=lambda r: -r["Cost"]):
        print(f"{r['ServiceName'] or '(none)':<34}{r['Cost']:>10.2f}")
    print(f"\n{'day':<12}{'cost':>10}")
    for r in sorted(by_day, key=lambda r: r["UsageDate"]):
        d = str(r["UsageDate"])
        print(f"{d[:4]}-{d[4:6]}-{d[6:8]}  {r['Cost']:>10.2f}")

    out = REPO_ROOT / "reports" / "cost-actual.json"
    out.write_text(json.dumps({"resource_group": rg, "from": args.start, "to": args.end,
                               "currency": cur, "total": round(total, 4),
                               "by_service": by_service, "by_day": by_day}, indent=2))
    print(f"\nwrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())