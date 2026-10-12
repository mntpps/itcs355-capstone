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
**Seed variance:** zero; the model has no randomness, verified with a second seed.
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

## How to run *(coming)*

## Deployment *(coming)*

## Monitoring and alerts *(coming)*

## Deliberate failure: frozen sensor *(coming)*

## Cost *(coming)*