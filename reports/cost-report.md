# Cost report

**Actual spend, resource group `itcs355-6688020`, 2026-09-13 .. 2026-10-20 (38 days): 6.85 USD.**
Source: Azure Cost Management (`python scripts/cost_actual.py --from 2026-08-01`; raw rows in `reports/cost-actual.json`). Cost data lags by up to a day, so Oct 20 is incomplete.

## Where the money went

| Service | Cost (USD) | Share | What it is |
|---|---:|---:|---|
| Container Registry | 6.11 | 89% | ACR **Basic**, a fixed fee per day whether used or not |
| Virtual Machines | 0.43 | 6% | the Azure ML training cluster (study, v1 training, retraining) |
| Storage | 0.11 | 2% | DVC remote and serving data, Azure ML workspace storage |
| Virtual Network | 0.08 | 1% | networking of the training cluster |
| Azure Monitor | 0.06 | 1% | the scheduled log-alert rule (since Oct 16) |
| Azure Container Apps | 0.06 | 1% | the service plus three scheduled jobs (refresh, monitor, retrain) |
| Key Vault, Log Analytics, Bandwidth, Load Balancer | 0.00 | 0% | within free allowances |
| **Total** | **6.85** | | |

**Per day:** a flat **≈ 0.17 USD every day** (the registry), with small bumps on training days: Sep 17 and Sep 20–24 (the study), Oct 12, and Oct 18 (the first retraining run). Nothing else shows up at daily resolution.

## What this says

1. **The bill is almost all a fixed fee, not work.** 89% is the registry existing. Everything that *does* something (serving, refresh, hourly monitoring, weekly retraining) cost **0.06 USD** in Container Apps, because it all scales to zero and fits within the monthly free grant (`reports/cost-serving.md`).
2. **Training is cheap.** The whole study (about 20 trials), the production model and the first retraining run together cost **0.43 USD** of compute.
3. **Monitoring costs less than one cent a day.** The alert rule (≈ 0.015 USD/day) is the only monitoring cost; log ingestion stays inside Log Analytics' free allowance.

## Monthly cost if left running as it is now

| Item | USD / month | Basis |
|---|---:|---|
| Container Registry (Basic) | 5.00 | fixed daily fee × 30 |
| Azure Monitor alert rule | ≈ 0.40 | 0.06 USD over Oct 16–20 |
| Weekly retraining (cluster) | ≈ 0.25 | the Oct 18 run, × 4 |
| Container Apps (service + jobs) | ≈ 0.30 | 0.06 USD since Oct 14, × 30/6 |
| Storage | ≈ 0.10 | 0.11 USD over 38 days |
| **Total** | **≈ 6 USD / month** | |

Serving traffic is not in this estimate because there is no real traffic yet. `reports/cost-serving.md` shows that the free grant covers about 100 active replica-hours a month; beyond that, it costs about 0.000021 USD per replica-second.

## If cost mattered

- **The registry is the only lever worth pulling.** A free registry (for example GitHub Container Registry) would remove 83% of the monthly cost. The trade-off: pulling from it needs a stored token, while ACR is pulled with a **managed identity and no secret**. For 5 USD a month, no secret is the better deal.
- Cheaper compute would save cents; it is not worth the complexity.

## Teardown

After the course (Oct 30), deleting the registry stops the fixed fee, and disabling the three scheduled jobs and the alert rule stops everything else. The service already costs nothing when idle. The teardown checklist is in the README.