"""Canary analysis: send traffic, compare the candidate revision with the stable one,
then promote or roll back BY RULE.

    python scripts/canary.py            # measure and decide, change nothing
    python scripts/canary.py --apply    # ...and apply the decision

Rule (fixed before any canary runs):
  PROMOTE only if the candidate served >= 20 requests and
    1. its error rate <= the stable revision's,
    2. its p95 latency <= 500 ms,
    3. for the same model version it returns exactly the same forecast for every station
       (a code-only change must not change predictions).
  Otherwise ROLL BACK. Fewer than 20 candidate requests: INCONCLUSIVE, change nothing.
  Errors that cannot be attributed to a revision are counted against the candidate.
Writes reports/canary-<candidate>.md.
"""
import argparse
import asyncio
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from src import config  # noqa: E402

APP = "itcs3556688020-forecast"
MIN_CANDIDATE_REQUESTS = 20
MAX_P95_MS = 500


def az(*args):
    r = subprocess.run([shutil.which("az"), *args, "-o", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"az {' '.join(args[:3])} failed:\n{r.stderr}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def pct(values, p):
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)] if s else float("nan")


def current_split(rg: str):
    url = (f"https://management.azure.com/subscriptions/{os.environ['AZURE_SUBSCRIPTION_ID']}"
           f"/resourceGroups/{rg}/providers/Microsoft.App/containerApps/{APP}?api-version=2024-03-01")
    app = az("rest", "--method", "get", "--url", url)
    entries = [t for t in app["properties"]["configuration"]["ingress"]["traffic"] if t["weight"] > 0]
    if len(entries) != 2:
        sys.exit(f"no canary in progress (traffic: {entries})")
    entries.sort(key=lambda t: t["weight"], reverse=True)
    name = lambda t: t.get("revisionName") or app["properties"]["latestRevisionName"]  # noqa: E731
    fqdn = app["properties"]["configuration"]["ingress"]["fqdn"]
    return name(entries[0]), entries[0]["weight"], name(entries[1]), entries[1]["weight"], f"https://{fqdn}"


async def call(client, url, sid):
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{url}/predict", json={"station_id": sid})
        body = r.json() if r.status_code == 200 else {}
        rev = r.headers.get("x-revision") or body.get("revision") or "unknown"
        return {"rev": rev, "status": r.status_code, "ms": (time.perf_counter() - t0) * 1000,
                "station": sid, "forecast": body.get("pm25_forecast"), "model": body.get("model_version")}
    except httpx.HTTPError:
        return {"rev": "unknown", "status": 0, "ms": (time.perf_counter() - t0) * 1000,
                "station": sid, "forecast": None, "model": None}


async def run(url, stable, cand, n, concurrency):
    async with httpx.AsyncClient(timeout=60) as client:
        stations = [f["station_id"] for f in (await client.post(f"{url}/predict/batch", json={})).json()["forecasts"]]
        seen = {stable: 0, cand: 0}
        for _ in range(300):  # warm-up (not measured): until both revisions have answered 3 times
            r = await call(client, url, random.choice(stations))
            seen[r["rev"]] = seen.get(r["rev"], 0) + 1
            if seen[stable] >= 3 and seen[cand] >= 3:
                break
        results, remaining = [], n

        async def worker():
            nonlocal remaining
            while remaining > 0:
                remaining -= 1
                results.append(await call(client, url, random.choice(stations)))

        await asyncio.gather(*(worker() for _ in range(concurrency)))
    return results


def summary(rows):
    ms = [r["ms"] for r in rows]
    errors = sum(1 for r in rows if r["status"] != 200)
    return {"n": len(rows), "errors": errors, "error_rate": errors / len(rows) if rows else 0.0,
            "p50": pct(ms, 50), "p95": pct(ms, 95)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=400)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--apply", action="store_true", help="apply the decision to live traffic")
    args = ap.parse_args()
    config.load()
    rg = os.environ["PROJECT_ID"]
    random.seed(20261015)

    stable, w_stable, cand, w_cand, url = current_split(rg)
    print(f"stable {stable} ({w_stable}%)  candidate {cand} ({w_cand}%)")
    results = asyncio.run(run(url, stable, cand, args.requests, args.concurrency))

    s_rows = [r for r in results if r["rev"] == stable]
    c_rows = [r for r in results if r["rev"] == cand]
    unknown = [r for r in results if r["rev"] not in (stable, cand)]
    s, c = summary(s_rows), summary(c_rows)
    c_errors = c["errors"] + sum(1 for r in unknown if r["status"] != 200)
    c_error_rate = c_errors / max(c["n"], 1)

    s_models = {r["model"] for r in s_rows if r["model"]}
    c_models = {r["model"] for r in c_rows if r["model"]}
    same_model = s_models == c_models and len(s_models) == 1
    stable_fc = {}
    for r in s_rows:
        if r["forecast"] is not None:
            stable_fc.setdefault(r["station"], set()).add(r["forecast"])
    compared = [r for r in c_rows if r["forecast"] is not None and r["station"] in stable_fc]
    mismatches = [r for r in compared if r["forecast"] not in stable_fc[r["station"]]]

    checks = [
        (f"candidate served >= {MIN_CANDIDATE_REQUESTS} requests", c["n"] >= MIN_CANDIDATE_REQUESTS, f"{c['n']}"),
        ("error rate <= stable", c_error_rate <= s["error_rate"],
         f"{c_error_rate:.1%} vs {s['error_rate']:.1%}"),
        (f"p95 <= {MAX_P95_MS} ms", c["p95"] <= MAX_P95_MS, f"{c['p95']:.0f} ms (stable {s['p95']:.0f} ms)"),
        ("same forecasts as stable (same model)",
         (not mismatches) if same_model else True,
         f"{len(compared) - len(mismatches)}/{len(compared)} match" if same_model
         else "skipped: different model versions"),
    ]
    if c["n"] < MIN_CANDIDATE_REQUESTS:
        decision = "INCONCLUSIVE"
    else:
        decision = "PROMOTE" if all(ok for _, ok, _ in checks) else "ROLLBACK"

    print(f"\n{'':10}{'requests':>9}{'errors':>8}{'p50 ms':>9}{'p95 ms':>9}")
    for label, x in (("stable", s), ("candidate", c)):
        print(f"{label:10}{x['n']:>9}{x['errors']:>8}{x['p50']:>9.0f}{x['p95']:>9.0f}")
    print(f"unattributed: {len(unknown)}\n")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if mismatches:
        m = mismatches[0]
        print(f"  e.g. station {m['station']}: candidate {m['forecast']} vs stable {sorted(stable_fc[m['station']])}")
    print(f"\nDECISION: {decision}")

    applied = "not applied (dry run)"
    if args.apply and decision in ("PROMOTE", "ROLLBACK"):
        target, other = (cand, stable) if decision == "PROMOTE" else (stable, cand)
        t0 = time.perf_counter()
        az("containerapp", "ingress", "traffic", "set", "-g", rg, "-n", APP,
           "--revision-weight", f"{target}=100", f"{other}=0")
        applied = f"traffic 100% -> {target} in {time.perf_counter() - t0:.1f} s"
        if decision == "ROLLBACK":
            az("containerapp", "revision", "deactivate", "-g", rg, "-n", APP, "--revision", cand)
            applied += f"; {cand} deactivated"
        else:
            applied += f"; {stable} kept active at 0% as the rollback target"
        print(applied)

    lines = [f"# Canary: {cand}", "",
             f"- When: {datetime.now():%Y-%m-%d %H:%M}; split {w_stable}/{w_cand} "
             f"(stable `{stable}` / candidate `{cand}`)",
             f"- {args.requests} measured requests, concurrency {args.concurrency}, random stations",
             "", "| revision | requests | errors | p50 ms | p95 ms |", "|---|---|---|---|---|",
             f"| stable | {s['n']} | {s['errors']} | {s['p50']:.0f} | {s['p95']:.0f} |",
             f"| candidate | {c['n']} | {c['errors']} | {c['p50']:.0f} | {c['p95']:.0f} |",
             f"| unattributed | {len(unknown)} | {sum(1 for r in unknown if r['status'] != 200)} | | |",
             "", *[f"- [{'PASS' if ok else 'FAIL'}] {n}: {d}" for n, ok, d in checks],
             "", f"**Decision: {decision}** — {applied}"]
    out = REPO_ROOT / "reports" / f"canary-{cand.split('--')[-1]}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())