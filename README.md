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

## Cost *(coming)*