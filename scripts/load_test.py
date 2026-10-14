"""Load-test the forecast service at several concurrency levels; report latency percentiles.

    python scripts/load_test.py --url https://<app> --levels 1 10 50 --requests 300

Target, stated BEFORE measuring: p95 <= 500 ms and 0 errors at concurrency 1 and 10.
Concurrency 50 is a stress level, reported but not part of the target.
Writes reports/load-test.md.
"""
import argparse
import asyncio
import math
import random
import time
from datetime import datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_P95_MS = 500
TARGET_LEVELS = (1, 10)


def pct(values, p):
    """Nearest-rank percentile."""
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


async def run_level(client, url, stations, concurrency, n):
    latencies, codes, revisions = [], {}, {}
    remaining = n

    async def worker():
        nonlocal remaining
        while remaining > 0:
            remaining -= 1
            t0 = time.perf_counter()
            try:
                r = await client.post(f"{url}/predict", json={"station_id": random.choice(stations)})
                code = r.status_code
                if code == 200:
                    rev = r.json().get("revision", "?")
                    revisions[rev] = revisions.get(rev, 0) + 1
            except httpx.HTTPError as exc:
                code = type(exc).__name__
            latencies.append((time.perf_counter() - t0) * 1000)
            codes[code] = codes.get(code, 0) + 1

    t0 = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    wall = time.perf_counter() - t0
    ok = codes.get(200, 0)
    return {"concurrency": concurrency, "requests": n, "errors": n - ok,
            "p50": pct(latencies, 50), "p95": pct(latencies, 95), "p99": pct(latencies, 99),
            "max": max(latencies), "rps": n / wall, "wall_s": wall,
            "codes": codes, "revisions": revisions}


async def main_async(args):
    url = args.url.rstrip("/")
    random.seed(20261014)
    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=max(args.levels))) as client:
        ready = (await client.get(f"{url}/ready")).json()
        if not ready.get("ready"):
            raise SystemExit(f"service not ready: {ready}")
        stations = [f["station_id"] for f in (await client.post(f"{url}/predict/batch", json={})).json()["forecasts"]]
        for _ in range(20):  # warm-up, not measured
            await client.post(f"{url}/predict", json={"station_id": random.choice(stations)})
        results = []
        for c in args.levels:
            res = await run_level(client, url, stations, c, args.requests)
            results.append(res)
            print(f"c={c:>3}  p50 {res['p50']:6.0f} ms  p95 {res['p95']:6.0f} ms  p99 {res['p99']:6.0f} ms  "
                  f"max {res['max']:6.0f} ms  {res['rps']:5.1f} req/s  errors {res['errors']}")
            await asyncio.sleep(5)
    return ready, stations, results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--levels", type=int, nargs="+", default=[1, 10, 50])
    ap.add_argument("--requests", type=int, default=300)
    args = ap.parse_args()
    ready, stations, results = asyncio.run(main_async(args))

    passed = all(r["p95"] <= TARGET_P95_MS and r["errors"] == 0
                 for r in results if r["concurrency"] in TARGET_LEVELS)
    lines = [
        "# Load test",
        "",
        f"- When: {datetime.now():%Y-%m-%d %H:%M} (from a laptop in Bangkok; region southeastasia)",
        f"- Revision: `{ready.get('revision')}`  image `{ready.get('image')}`",
        f"- Model: {ready['model']} v{ready['model_version']}, data `{ready['data_version']}`",
        f"- Requests per level: {args.requests}, random station from {len(stations)}; 20 warm-up requests not measured",
        f"- **Target (set before measuring): p95 <= {TARGET_P95_MS} ms and 0 errors at concurrency "
        f"{', '.join(map(str, TARGET_LEVELS))}. Result: {'MET' if passed else 'NOT MET'}**",
        "",
        "| concurrency | p50 ms | p95 ms | p99 ms | max ms | req/s | errors | status codes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['concurrency']} | {r['p50']:.0f} | {r['p95']:.0f} | {r['p99']:.0f} | {r['max']:.0f} "
                     f"| {r['rps']:.1f} | {r['errors']} | {r['codes']} |")
    out = REPO_ROOT / "reports" / "load-test.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\ntarget {'MET' if passed else 'NOT MET'}; wrote {out.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()