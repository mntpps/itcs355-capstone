# Post-mortem: serving data went stale (2026-10-15 to 2026-10-17)

**Status:** resolved · **Severity:** Sev1 (critical finding) · **Author:** Bhornpailin Chunkasut · Blameless format: this is about the system and the process, not about people.

## Summary

The forecast service went live on Oct 15 with the data snapshot pinned in git, whose last day was Oct 11. Nothing ever updated the serving data after that, so every day the forecast fell one day further behind. The first live run of the hourly monitor detected this on Oct 16 (`stale_data`, data 5 days old) and emailed an alert. The root-cause fix was a daily refresh job with a versioned manifest, which the service picks up without a redeploy. It went live on Oct 17 at 19:03, and the alert resolved itself at 20:48.

A second, smaller incident happened along the way: a fix made on Oct 16 (replay fixtures in the image) was committed to a branch *after* that branch's PR had been merged, so it never reached `main`. A rebuild on Oct 17 silently dropped it.

## Impact

- **Users:** none outside the project. All traffic in the period was our own canary and load tests (Oct 15: 502 requests; Oct 17: 833).
- **What would have happened with real users:** the service would have answered "tomorrow's forecast" with a forecast for **Oct 12**, getting older every day. The response carries `forecast_date`, so the error was visible, but nobody was alerted to look.
- **Model:** unaffected. The same model (v1) served throughout.

## Timeline (Bangkok time)

| When | What |
|---|---|
| Oct 15 15:36 | Service deployed (revision `m1-488ae00`). Data: git pointer, last observation Oct 11. `/ready` already reported `data_age_days`, but nothing checked it. |
| Oct 16 ~21:00 | Hourly monitor job deployed. **First live run: `stale_data` critical, data 5 days old.** |
| Oct 16 21:02 | Alert email "Fired: Sev1 … PM2.5 monitor finding" (stateful log alert, 15-min evaluation). |
| Oct 16 21:17–21:32 | Monitor replay of a recorded frozen-sensor incident (unrelated check). The first attempt failed: fixtures missing from the image. Fixed by adding them to the Dockerfile (commit `acf296f` on `monitor/job`). See incident 2. |
| Oct 16 23:00 – Oct 17 01:00 | Daily refresh designed and written (`src/refresh.py`). Local dry run: 461/461 overlapping station-days identical to `ingest build`. |
| Oct 17 17:25 | Least-privilege identity `itcs3556688020-refresh` created: the only identity allowed to write data. |
| Oct 17 18:17 | Cloud dry run: reads blob storage, uploads nothing. OK. |
| Oct 17 18:24 | Build log shows no `tests/fixtures` step. Found: `acf296f` was never merged (incident 2). Cherry-picked. |
| Oct 17 18:56 | **Code first:** the service's data-reload code was canaried while no manifest existed, so both revisions served the same data. 70/70 identical forecasts, promoted. |
| Oct 17 19:03 | **Data second:** first manifest published (`serving/current.json`, last date Oct 16). |
| Oct 17 ~19:05 | Service serving the manifest: `data_from: manifest`, `data_age_days: 1`. |
| Oct 17 20:48 | Alert **Resolved** automatically (no finding in the 2-hour window). |
| Oct 18 06:30 | First **scheduled** refresh succeeded; new `data_version`, no redeploy. |

**Time to detect:** immediate once monitoring existed; about 29 hours after the first deploy. **Time to fix:** about 22 hours from the alert to the first publish. **Time to resolve:** 23 h 46 min from alert fired to alert resolved.

## Root cause (5 whys)

1. *Why was the forecast stale?* The serving data was the snapshot named by the `.dvc` pointer in git, and that never changed.
2. *Why did it never change?* No process refreshed serving data. `ingest update` existed, but it was a manual command on a laptop.
3. *Why was that not caught at deploy?* The design treated the serving **input** like part of the **release**, pinned with the model. That is right for training data and wrong for the daily readings a forecast needs.
4. *Why did nothing alert before Oct 16?* `/ready` exposed `data_age_days` from day one, but no check consumed it until the monitor job was deployed.
5. *Why was the monitor later than the service?* Deliberate ordering (serve first, then monitor), with no rule that a service may not go live without its freshness check.

**Root cause:** serving data had no owner process, and the signal that would have shown it (`data_age_days`) was produced but not watched.

## What went well

- **The monitor did its job on its first run**, and the alert pipeline (log, alert rule, email) worked end to end.
- **The fix reused training code**, so it added no new aggregation logic, and proved it: 461/461 identical days.
- **Fail-closed publishing:** too many failed stations, changed columns or fewer rows means nothing is published; the manifest is written last.
- **One change at a time:** the code was canaried before any data changed, so the canary's "identical forecasts" rule judged the code alone.
- **Lineage held throughout:** git pointer `85a15b41` → manifest `base` → manifest `files` `57428101` → `/ready` `data_version`.
- **The alert resolved itself**, which confirmed the fix from the outside rather than by our own judgement.

## What went badly

- **The service went live without a freshness check**, while the README already listed a freshness SLO. A target nobody measures is not an SLO.
- **The first fix took two work sessions.** Acceptable for a course project with no users; not acceptable as a pattern.
- **Incident 2:** the Oct 16 replay fix was committed and pushed after PR #8 was merged. GitHub merges what the branch contains when you click Merge, so the commit stayed on `monitor/job`. For a day, the running image contained code that was not on `main`. It was found only because a rebuild's log was missing a step.

## Incident 2 in short: a fix that was deployed but never merged

| | |
|---|---|
| What | Dockerfile line adding `tests/fixtures/` (commit `acf296f`), pushed to `monitor/job` after its PR had merged |
| Effect | The Oct 16 image had it; `main` did not; the Oct 17 rebuild from `main` dropped it |
| Found | Oct 17 18:24, reading the build log (11 steps instead of 12) |
| Fixed | `git cherry-pick acf296f` into the next PR; `git log main..origin/monitor/job` confirmed nothing else was stranded |
| Prevented | GitHub "Automatically delete head branches" turned on; stale branches deleted. A merged branch can no longer quietly collect commits. |

## Action items

| # | Action | Status |
|---|---|---|
| 1 | Daily refresh job with a versioned manifest; the service hot-reloads data | **done** Oct 17 |
| 2 | `stale_data` monitor check with an email alert | **done** Oct 16 |
| 3 | `/ready` reports `data_from` and `data_check_error`; prediction logs carry `data_version` | **done** Oct 17 |
| 4 | Auto-delete merged branches | **done** Oct 18 |
| 5 | Freshness on the dashboard (daily refresh tile) | **done** Oct 19 |
| 6 | Alert when a scheduled job execution **fails** (refresh, retrain, monitor). Today a failed refresh is caught only indirectly, by `stale_data`, a day later. | open |
| 7 | Deploy rule: `deploy_service.py` refuses to deploy when `/ready` of the new revision reports `data_age_days > 1` | open |
| 8 | Restrict the refresh identity's writes to `capstone/serving/` with an Azure ABAC condition | open |

## Lessons

1. **Training data is a release artifact; serving input is not.** Pin the first, and give the second an owner process.
2. **A signal nobody watches is not monitoring.** `data_age_days` existed from the first deploy.
3. **Change one thing at a time,** so each check judges one change.
4. **"Merged" means what was on the branch at the moment of merging.** Delete branches after merge.