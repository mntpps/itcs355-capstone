# Data findings — Bangkok PM2.5 (Air4Thai via OpenAQ)

Exploration done on 2026-10-11, before building the pipeline. Every number below comes
from scripts in `explore/` and can be reproduced:

    python explore/find_stations.py      # which stations exist
    python explore/download_daily.py     # daily history → data/daily/*.csv
    python explore/analyze_daily.py      # the analysis below

## Source

- **OpenAQ API v3**, provider **Air4Thai** (Thailand Pollution Control Department),
  reference monitors only (`monitor=true`).
- Bangkok bounding box `100.33,13.49,100.94,13.96` (lon/lat).
- OpenAQ lists **no licence** for the Air4Thai locations (`licenses: null`). The project
  credits the Pollution Control Department as the original source.
- Daily values are aggregated by OpenAQ from hourly readings; a day runs from
  00:00 to 24:00 **Bangkok time** (+07:00), which matches the 01:00 batch job.
- Air4Thai readings appear to be **whole numbers** (e.g. 15.0, 29.0), so identical
  consecutive hourly readings happen naturally more often than with decimal data.

## Stations and coverage

- 191 PM2.5 locations in the box; **83 Air4Thai reference monitors are live**
  (data on 2026-10-11).
- Some stations list **two PM2.5 sensors**: one with full history and one almost empty
  (e.g. Lumpini Park: sensor 2076227 has data, 16312736 has none since 2026-09-28).
  → The ingest step must choose, per station, the sensor that has recent data —
  never "the first PM2.5 sensor", and never a hard-coded sensor ID.
- Window 2023-01-01 to 2026-10-10 = **1,379 possible days**. Most stations have
  **950–1,200 days (70–87%)**; none is above 87%.
- Excluded for now: **Wacchirabenchatat Park** (344 days) and **Public Park to
  commemorate the sixth anniversary…** (192 days, sensor only since 2026-03).
  Rama VIII Park (833 days) is borderline.
- Dataset: 87,433 station-days.

## Finding 1 — the whole feed goes down, sometimes for weeks

274 of 1,379 days had fewer than half of the stations reporting. The longest gaps had
**zero** stations reporting, so the cause is the feed (OpenAQ's import or Air4Thai
itself), not individual sensors:

| Outage | Days |
|---|---|
| 2023-02-17 to 2023-03-18 | 30 |
| 2025-04-26 to 2025-05-06 | 11 |
| 2025-07-02 to 2025-08-24 | 54 |
| 2025-10-11 to 2025-11-18 | 39 |
| 2026-07-03 to 2026-07-29 | 27 |

Consequences for the design:
- The 06:00 freshness SLO **will** be broken by upstream outages. The batch job must
  detect "fewer than half the stations reported yesterday", raise a **feed-outage
  alert**, and must not silently forecast from stale inputs.
- Gaps bias averages: March 2023 shows only 22.6 µg/m³ because most of mid-Feb to
  mid-March is missing. Evaluation must report how many days each period actually has.
- A backup source (Air4Thai's own feed) is worth investigating — not yet verified.

## Finding 2 — frozen sensors happen for real

66 station-days where every reading that day was identical (`min == max`,
coverage ≥ 50%). Value counts of those days:

| Stuck value | Days | Interpretation |
|---|---|---|
| **15.0** | 35 | Likely a placeholder written when a sensor fails (hypothesis) |
| **0.0** | 9 | "No data" written as zero — physically impossible for a whole day |
| 19–33, various | ~22 | Ambiguous: short freezes, or genuinely calm days |

Two real episodes at 15.0:
- **Bang Sue District Office** — every reporting day from **2023-09-05 to 2023-09-30**
  (21 days). The missing dates in between have no data at all.
- **Mahaisawan Intersection** — **2024-06-20 to 2024-07-02** (12 days).

Why it matters: 15.0 sits right on the **normal rainy-season average** (12–18 µg/m³,
June–September). Range checks pass, drift detection sees nothing unusual, and the
forecast looks plausible. Only a check for *readings that never change* catches it.

Other frozen days by station: National Economic and Social Development Council
Office 9 (probably the 0.0 days — to confirm), Khlong Toei 2, Huai Khwang 2,
Bangrak Lovely Plaza 2, Army Apartment Sam Sen 2, others 1 each.

Consequences for the design:
- **Stuck check:** flag a station when its hourly readings are identical for N hours
  in a row. **N is not chosen yet** — it must sit above the longest run of identical
  readings seen at healthy stations (next step: hourly analysis).
- **Zero check:** a daily average below ~1 µg/m³ is a broken sensor, not clean air.
- **The same rules clean the training data** — frozen days are removed before
  training, so the model never learns from fake values.
- **Tests from real incidents:** Bang Sue Sept 2023 and Mahaisawan Jun–Jul 2024
  become test cases the stuck check must flag.

## Finding 3 — strong seasonality

Average PM2.5 across all stations, by month (µg/m³):

| | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2023 | 34.8 | 51.8 | 22.6* | 38.1 | 24.2 | 15.3 | 14.3 | 16.6 | 15.5 | 20.1 | 27.4 | 32.2 |
| 2024 | 41.5 | 33.3 | 25.8 | 25.1 | 21.7 | 13.3 | 13.3 | 14.0 | 18.2 | 21.0 | 25.5 | 33.3 |
| 2025 | 47.0 | 36.9 | 26.0 | 19.7 | 15.2 | 14.2 | 18.3* | 13.2* | 13.0 | 15.7* | 26.0* | 29.4 |
| 2026 | 37.2 | 31.4 | 25.5 | 25.0 | 15.9 | 12.3 | 12.7* | 12.1 | 13.8 | 21.6 | | |

\* month affected by a feed outage — average based on fewer days.

- **Peak: January–February** (33–52). **Low: June–September** (12–18).
- A model trained mostly on rainy months will meet very different data in January:
  real, seasonal drift that the monitoring must expect.

## Open questions

- Choose N for the stuck check from hourly data (next step).
- Confirm that the 0.0 days belong to the NESDC station.
- Check whether Air4Thai's own feed can serve as a backup during OpenAQ outages,
  and under what terms.

## Finding 4 — choosing N for the stuck check

`explore/hourly_runs.py`: runs of identical consecutive hourly readings. A run breaks
when the value changes or an hour is missing.

Healthy stations (Din Dang, Lumpini, Bang Phlat, Thai Meteorological Dept, Chatuchak,
Khlong Toei, Odeon Circus, Nong Chok — all of 2024, ~48,000 hours, 43,123 runs):

| Run length | Count |
|---|---|
| ≥ 3 h | 754 |
| ≥ 4 h | 153 |
| ≥ 5 h | 22 |
| ≥ 6 h | 4 |
| ≥ 8 h | **0** |

99th percentile 3 h · 99.9th percentile 4 h · longest 7 h.

Known frozen episodes:
- Mahaisawan, Jun 2024 — longest run **110 h**.
- Bang Sue, Sep 2023 — longest run **14 h** (then 13, 11, 10, 9): the freeze is split
  by missing hours, which restart the count.

**Decision: N = 8 hours.** One hour above the longest healthy run, below every fragment
of the Bang Sue freeze. N = 6 would give ~4 false alarms per 8 stations per year
(~40/year across all stations).

Caveat: based on 8 stations × 1 year. To validate on all 83 stations when the pipeline
is built; raise to N = 10 if any healthy station exceeds 7 h.
Possible improvement: join runs across 1–2 h gaps so a gappy freeze counts as one run.