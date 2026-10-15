# Serving cost

- Prices: Azure Retail Prices API, Container Apps Consumption (Standard), southeastasia, fetched 2026-10-15 (USD, list price)
- Replica: 0.5 vCPU, 1.0 GiB. Throughput 39.3 req/s per replica (reports/load-test.md)
- Active $0.000021/replica-second, idle $0.000006/replica-second, requests $0.40/million

| scenario | cost (USD) |
|---|---|
| 1,000 predictions (replica busy at full throughput) | $0.0009 |
| One wake-up from zero (served, then 300 s cooldown) | $0.0063 |
| One replica kept warm for a day, idle (minReplicas 1) | $0.5184 |
| One replica busy for a full day | $1.8144 |

Monthly free grant (180,000 vCPU-s, 360,000 GiB-s, 2,000,000 requests) covers about **100 active replica-hours** of this app.
