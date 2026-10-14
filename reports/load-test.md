# Load test

- When: 2026-10-14 15:07 (from a laptop in Bangkok; region southeastasia)
- Revision: `itcs3556688020-forecast--m1-e9317fe-10141502`  image `itcs3556688020.azurecr.io/itcs355@sha256:e9317fe6bfe72aa94d8042833e56015143ef12af982cfdbfe5640ca02ca67be1`
- Model: pm25-forecast v1, data `85a15b410325192c754ebe25de0a4c93`
- Requests per level: 300, random station from 77; 20 warm-up requests not measured
- **Target (set before measuring): p95 <= 500 ms and 0 errors at concurrency 1, 10. Result: MET**

| concurrency | p50 ms | p95 ms | p99 ms | max ms | req/s | errors | status codes |
|---|---|---|---|---|---|---|---|
| 1 | 44 | 48 | 51 | 54 | 22.5 | 0 | {200: 300} |
| 10 | 278 | 382 | 425 | 524 | 39.3 | 0 | {200: 300} |
| 50 | 1358 | 1558 | 1694 | 1699 | 37.9 | 0 | {200: 300} |
