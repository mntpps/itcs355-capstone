# Next-day PM2.5 forecast for Bangkok

ITCS355 MLOps capstone · Bhornpailin Chunkasut (6688020)

Forecasts each Bangkok air-quality station's PM2.5 for the day, published by 06:00. A batch
job runs at 01:00 using the previous day's complete data; an API serves the forecasts.

## Data

- **PM2.5:** Pollution Control Department of Thailand (Air4Thai), via the OpenAQ API v3.
  81 stations (rule in `scripts/make_stations.py`). Hourly values are 24-hour moving
  averages, so the daily target is the 23:00 reading (`docs/data-findings.md`, Finding 7).
- **Weather:** Open-Meteo Previous Runs API (CC BY 4.0), day-2 forecasts. Currently not a
  model input because of drift between years (Finding 8); kept as a monitored signal.
- **Versioning:** DVC, remote on Azure Blob (`.dvc/config`). `dvc pull` restores the exact data.

## Model selection

Selected: **study-17**, gradient-boosted trees predicting the day-to-day change,
absolute-error loss, learning rate 0.03, 15 leaves, min 200 rows per leaf, and the
predicted change scaled by 0.25 toward persistence. Selection rule, fixed before the study:
the lowest validation MAE among trials passing all four gate checks on validation (the
2024–25 dry season). The top validation score (k = 0.5, MAE 5.843) was not chosen: it
missed the recall check by 0.3 points. Three trials passed the gate on the test year but not
on validation; none were chosen, since selecting on test would make the test meaningless.
**Seed variance:** not zero. With more than 10,000 training rows, scikit-learn's `HistGradientBoostingRegressor` turns on early stopping automatically, and the seed chooses its random 10% hold-out. Five seeds of study-17 (20260101, 1–4) give val MAE 5.901 ± 0.028 (range 5.877–5.946) and test MAE 3.964 ± 0.009 (3.952–3.976); all five pass both gates. The worst seed still beats persistence (val 6.14, test 4.12), so the improvement is not seed luck. The registered seed (20260101) was fixed before the study.
**How narrow the selection was:** study-17 was the only trial to pass the validation gate. study-18 (k=0.5) had lower val MAE (5.843 vs 5.889) but recall 0.734, just below persistence's 0.737; study-17's 0.739 passed by 0.002. Both margins are within seed noise, so the choice between them is narrow. study-17 passed the gate with all five seeds. The rule (best val MAE among gate-passers) and the seed were fixed before the study, and we did not re-run study-18 under other seeds to change the outcome — that would be selecting on results. Cost of the choice: lower onset recall (0.075 vs 0.121), i.e. slower to catch the first day of an episode.
**Cost:** a full fit takes about a minute on a laptop; monthly retraining cost is in the
cost report. **How this could be wrong:** the margin over persistence is small (MAE −4%,
recall +0.2 points), validation covers one dry season, and station-days are correlated,
so the improvement may be partly noise. The model is mostly persistence and catches few
pollution onsets (7%).

Full comparison: `reports/study-comparison.md` · experiment `pm25-study` in Azure ML.

## Evaluation gate

A model is used only if, against persistence ("tomorrow = today") on the same rows, it has
lower MAE, lower dry-season MAE, recall on days > 37.5 µg/m³ at least as high, and a
false-alarm rate no higher (`src/evaluate.py`). If nothing passes, persistence stays in service.

## Identities (least privilege)

| Identity | Can do | Role and scope | Why it is needed |
|---|---|---|---|
| Training cluster (`itcs3556688020-cpu`, system-assigned managed identity) | Pull the training image | `AcrPull` on the container registry only | Jobs run the image by digest; read-only, cannot push or delete |
| | Read the versioned data | `Storage Blob Data Reader` on the storage account only | Job inputs are DVC objects addressed by md5; training never writes data |

Evidence: before `AcrPull` was granted, the first job failed with "Failed to pull Docker image … could
not authenticate with the Docker registry" (job `gray_lizard_1nk701bx42`). The registry's admin
password was deliberately not enabled: one shared credential with full access is the opposite of
least privilege. A scheduled run uses this identity, not the developer's login.

## Promotion (staging → production)

- **Staging:** `scripts/register.py` registers a version only if the run passed the gate and has complete lineage; it is tagged `stage: staging`. The registry holds *candidates*.
- **Production:** the version that serves is whatever `config/production.json` names. That file is the single source of truth.
- **How to promote:** `python scripts/promote.py --version N --reason "..."` refuses versions without full lineage or not in staging, then rewrites the file. The change reaches production only by a merged pull request (first promotion: PR #1).
- **Who may promote:** anyone may open the PR; merging is the approval. Here the single author approves; in a team, branch protection would require a second reviewer and passing CI.
- **Rollback:** revert the promotion commit. `previous_version` in the file records what to fall back to.

## How to run *(coming)*

## Deployment *(coming)*

## Monitoring and alerts *(coming)*

## Deliberate failure: frozen sensor *(coming)*

## Serving cost

From `scripts/cost_serving.py` (Azure Retail Prices API, Container Apps Consumption, southeastasia; replica 0.5 vCPU / 1 GiB; throughput 39.3 req/s from the load test) — full table in `reports/cost-serving.md`:

- **1,000 predictions: ~$0.0009.** The work itself is nearly free.
- **What costs money is replicas existing:** one wake-up from zero ≈ $0.0063 (the replica stays for the 300 s cooldown); one replica kept warm all day ≈ $0.52 (~$15.50/month) even with zero requests.
- **Decision: scale to zero by default.** The forecast changes once a day and traffic is light; the monthly free grant covers ~100 active replica-hours, so realistic use costs ~$0. Trade-off: a 20–40 s cold start for the first request after a quiet period. `minReplicas 1` only for load tests and the live demo.
- **Lesson learned:** `minReplicas` belongs to a *revision*. A revision kept warm for the load test kept a replica running after traffic moved away, until it was explicitly deactivated.

## Deployment and canary

- **Deploy by digest** (`scripts/deploy_service.py`): one image for training and serving; refuses tags. Azure Container Apps (standard, ConsumptionOnly environment — the new *express* default cannot run two revisions, so it cannot canary). Revision name = model version + image digest + deploy time.
- **Canary**: `--canary 10` creates the new revision with 10% of traffic *in the same request*, so no user ever sees an untested revision at 100%.
- **Decision by written rule** (`scripts/canary.py`): promote only if the candidate served ≥ 20 requests, its error rate ≤ stable's, its p95 ≤ 500 ms, and — for the same model version — it returns **exactly the same forecast for every station**. Otherwise roll back. Errors that cannot be attributed to a revision count against the candidate. Latency is checked against the absolute target, not against stable: at 90/10 the stable revision queues more and always looks slower.
- **Evidence:**
  - Good candidate `488ae00` (structured logging): 0 errors, p95 73 ms, 39/39 forecasts identical → **promoted** (`reports/canary-m1-488ae00-10151536.md`).
  - Bad candidate `aa288ab` (branch `demo/bad-canary`, never merged: `shrink` not passed to `forecast.predict`): 0 errors, p95 75 ms — looked perfect — but **0/54 forecasts matched** → **rolled back in 14.8 s** (`reports/canary-m1-aa288ab-10151621.md`).
- **Defence in depth:** the same bug fails 3 unit tests (safety net 1: tests/CI before merge); the canary is safety net 2, in production, for what tests miss.


## Calling the service

Base URL: `https://itcs3556688020-forecast.agreeableforest-9e3a5ae0.southeastasia.azurecontainerapps.io`
(scales to zero: the first request after a quiet period takes ~20–40 s while the model loads).

| Endpoint | Purpose |
|---|---|
| `GET /health` | process alive (liveness) |
| `GET /ready` | model + data loaded (readiness): model version, data version, data age, revision, image |
| `POST /predict` | `{"station_id": "135"}` → tomorrow's PM2.5 for one station |
| `POST /predict/batch` | `{"station_ids": ["135", "200"]}`, or `{}` for every station |

```bash
curl -s https://<base-url>/ready
curl -s -X POST https://<base-url>/predict -H "Content-Type: application/json" -d '{"station_id":"135"}'
```

Example response:

```json
{"station_id": "135", "forecast_date": "2026-10-12", "pm25_forecast": 15.9, "yesterday_pm25": 15.4,
 "unhealthy": false, "threshold": 37.5, "model": "pm25-forecast", "model_version": "1",
 "revision": "itcs3556688020-forecast--m1-488ae00-10151536"}
```

Unknown station → 404; malformed body → 422; model not loaded yet → 503. Every response carries an `x-revision` header.
Interactive docs: `/docs`.


## CI (`.github/workflows/ci.yml`)

Every pull request to `main`, and every push to `main`, runs three checks:

- **secret-scan** — gitleaks over the **whole history** (`fetch-depth: 0`): a key deleted later is still in old commits.
- **lint-test** — installs the hashed lock (`pip install --require-hashes`), `ruff check` (rules pinned in `ruff.toml`), `pytest`.
- **build** — `docker build`, only after lint-test passes.

The job token is read-only (`permissions: contents: read`).

**Deliberately bad PRs, both caught by the responsible check and closed unmerged:**
- PR #3 — a private key committed: **secret-scan failed**. A first attempt with an obviously fake key ("demo", "fake") was *not* flagged — gitleaks ignores placeholders — so the test was redone with a real-format throwaway key that was never registered anywhere. A check is only trusted after it has been seen to fail.
- PR #4 — `shrink` not passed to `forecast.predict`: **lint-test failed** (3 tests), build skipped.

**Enforcement:** ruleset `protect-main` requires a PR and all three checks, with no bypass. GitHub enforces rulesets on private repositories only on paid plans, so enforcement is pending the GitHub Student Developer Pack; until then the rule is followed by practice.