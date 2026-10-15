"""Serving cost from Azure's Retail Prices API plus the measured load-test throughput.

    python scripts/cost_serving.py --rps 39.3

Writes reports/cost-serving.md. List prices before the monthly free grant.
"""
import argparse
from datetime import date
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
API = "https://prices.azure.com/api/retail/prices"
FILTER = ("serviceName eq 'Azure Container Apps' and armRegionName eq 'southeastasia' "
          "and priceType eq 'Consumption' and skuName eq 'Standard'")
VCPU, GIB = 0.5, 1.0                 # per replica, as set in scripts/deploy_service.py
COOLDOWN_S = 300                     # Container Apps default: idle seconds before scaling to zero
FREE = {"vcpu_s": 180_000, "gib_s": 360_000, "requests": 2_000_000}   # per subscription per month


def fetch_prices() -> dict:
    items = requests.get(API, params={"$filter": FILTER}, timeout=30).json()["Items"]
    p = {i["meterName"]: i["retailPrice"] for i in items}
    return {"vcpu_active": p["Standard vCPU Active Usage"], "mem_active": p["Standard Memory Active Usage"],
            "vcpu_idle": p["Standard vCPU Idle Usage"], "mem_idle": p["Standard Memory Idle Usage"],
            "per_request": p["Standard Requests"] / 1_000_000}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rps", type=float, required=True, help="sustained req/s of one replica (load test)")
    args = ap.parse_args()
    p = fetch_prices()

    active_s = VCPU * p["vcpu_active"] + GIB * p["mem_active"]   # $ per replica-second, active
    idle_s = VCPU * p["vcpu_idle"] + GIB * p["mem_idle"]         # $ per replica-second, idle
    per_1000 = 1000 / args.rps * active_s + 1000 * p["per_request"]
    rows = [
        ("1,000 predictions (replica busy at full throughput)", per_1000),
        ("One wake-up from zero (served, then 300 s cooldown)", COOLDOWN_S * active_s),
        ("One replica kept warm for a day, idle (minReplicas 1)", 86_400 * idle_s),
        ("One replica busy for a full day", 86_400 * active_s),
    ]
    free_hours = min(FREE["vcpu_s"] / VCPU, FREE["gib_s"] / GIB) / 3600

    lines = [
        "# Serving cost",
        "",
        f"- Prices: Azure Retail Prices API, Container Apps Consumption (Standard), southeastasia, "
        f"fetched {date.today()} (USD, list price)",
        f"- Replica: {VCPU} vCPU, {GIB} GiB. Throughput {args.rps} req/s per replica (reports/load-test.md)",
        f"- Active ${active_s:.6f}/replica-second, idle ${idle_s:.6f}/replica-second, "
        f"requests ${p['per_request'] * 1e6:.2f}/million",
        "",
        "| scenario | cost (USD) |",
        "|---|---|",
        *[f"| {name} | ${cost:.4f} |" for name, cost in rows],
        "",
        f"Monthly free grant ({FREE['vcpu_s']:,} vCPU-s, {FREE['gib_s']:,} GiB-s, "
        f"{FREE['requests']:,} requests) covers about **{free_hours:.0f} active replica-hours** of this app.",
    ]
    out = REPO_ROOT / "reports" / "cost-serving.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()