# Model card: `pm25-forecast` v1

| | |
|---|---|
| Model | `pm25-forecast` version 1 (Azure ML registry), `config/production.json` |
| Run | MLflow run `bright_seal_wsd8hyrstg`, code `2182393` |
| Configuration | study-17 (`scripts/train_remote.py`, `SELECTED`) |
| In production since | 2026-10-15 |
| Owner | Bhornpailin Chunkasut (6688020), ITCS355 |

## What it does

Forecasts **tomorrow's daily PM2.5** (µg/m³) for each Bangkok air-quality station, and flags it as **unhealthy** above the Thai 24-hour standard of **37.5 µg/m³**. Callers send only a station id; the service builds the features itself with the training code, so features cannot differ between training and serving.

**Intended use:** a day-ahead indication for the public and for planning (outdoor activity, masks, school notices), alongside official sources.

**Not intended for:** health decisions for an individual; legal or regulatory reporting (use Air4Thai / PCD directly); places other than the listed Bangkok stations; forecasts more than one day ahead.

## Data

- **Target:** Pollution Control Department (Air4Thai) PM2.5 via the OpenAQ API v3, 81 Bangkok stations. The daily value is the 23:00 reading of the station's 24-hour moving average (`docs/data-findings.md`, Finding 7).
- **Periods** (`src/split.py`; never random, with 7-day gaps so lag inputs cannot leak across splits):
  - train: 2024-01-02 .. 2024-10-24 and 2025-05-08 .. 2025-09-23
  - validation: 2024-11-01 .. 2025-04-30 (the 2024–25 dry season; used only to choose settings)
  - test: 2025-10-01 .. 2026-09-30 (a full year, strictly after everything the final model was fitted on)
- **Final fit:** train + validation. **The model has never seen data after 2025-09.**
- **Versioning:** DVC; the exact training files are recorded by md5 in the registry entry (`data_version`).
- **Weather** (Open-Meteo) is **not** an input: it drifted between years (Finding 8). It is kept as a monitored signal.

## Model

Gradient-boosted trees (`HistGradientBoostingRegressor`) that predict the **day-to-day change**, with absolute-error loss, learning rate 0.03, 15 leaves and at least 200 rows per leaf. The predicted change is **scaled by 0.25** before it is added to yesterday's value:

> forecast = yesterday + 0.25 × predicted change

So by design it stays close to persistence ("tomorrow = today") and moves away from it only cautiously. Inputs are the station's and the city's recent PM2.5 plus seasonal terms (feature set `no_weather`, `src/features.py`).

**Why this one:** the selection rule, fixed before the study, was the lowest validation MAE among trials that pass all four gate checks on validation. study-17 was the only one that passed. Full comparison: `reports/study-comparison.md`.

## Evaluation

The gate (`src/evaluate.py`): against persistence **on the same rows**, the model needs lower MAE, lower dry-season MAE, recall on days > 37.5 at least as high, and a false-alarm rate no higher.

| | Validation (2024–25 dry season) | Test (Oct 2025 – Sep 2026) |
|---|---|---|
| MAE: model | **5.889** | **3.976** |
| MAE: persistence | 6.14 | 4.12 |
| Improvement | −4.1% | −3.5% |
| Gate | passed | passed |

- **Seed variance:** 5 seeds give val MAE 5.901 ± 0.028 and test MAE 3.964 ± 0.009. All five pass both gates, and the worst seed still beats persistence.
- **Recall on unhealthy days (validation):** 0.739 vs persistence 0.737. It passes, but only by 0.002.
- **Onset recall** (the first bad day of an episode): **0.075**. The model catches very few first days.

## Since deployment (live, out of sample)

| Window | Rows | Model MAE | Persistence MAE | |
|---|---|---|---|---|
| Sep 20 – Oct 17, 2026 (retraining gate) | 2,084 | 3.988 | 4.186 | −4.7% |
| Oct 4 – 17, 2026 (`/performance`) | 1,061 | 4.679 | 4.995 | −6.3% |

Three different periods give the same direction: **no sign of decay**, a year after the training data ends.

## Limitations: how this could be wrong

- **The margin over persistence is small** (MAE −3.5% to −6%). Station-days are correlated, so part of it may be noise.
- **It mostly says "same as today".** By design (shrink 0.25) it is slow to react, so it rarely catches the **start** of a pollution episode (onset recall 7.5%). A forecast below 37.5 is **not** a promise of a safe day.
- **The selection was narrow:** study-18 had lower validation MAE and missed the recall check by 0.003. The choice between them is within seed noise.
- **Validation covers one dry season**, and the dry season is when PM2.5 matters most.
- **Rainy-season live results** (Oct 2026) say little about the dry season. Most days are well below 37.5, and the few high days are isolated spikes that no model or baseline caught.
- **Stations with no reading yesterday** get no forecast (HTTP 404) rather than a guess.

## How it is watched and replaced

- **Hourly:** the model is re-scored on the last 14 days with actuals (`model_vs_persistence`). A warning fires if it loses to persistence.
- **Daily:** fresh data at 06:30; `stale_data` alerts if the data is older than 1 day.
- **Weekly:** a challenger is retrained on the latest data and must beat **this** model on the same 28 recent days, by more than seed noise and with every seed. The first run (Oct 18) rejected it: challenger 4.060 vs v1 3.988.
- **Replacement** is never automatic: `promote.py` → pull request → CI → canary.
- **Fallback:** if no model passes the gate, persistence stays in service.