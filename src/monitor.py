"""Monitoring: is something wrong RIGHT NOW? Each check returns findings; a scheduled job
turns findings into alerts. Thresholds reuse src/checks.py (each tied to docs/data-findings.md).

Input `hourly` is a DataFrame with columns location_id, time (tz-aware), pm25.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src import checks

MAX_DATA_AGE_DAYS = 1          # forecast for tomorrow needs yesterday's data
REPORTING_WINDOW = pd.Timedelta(hours=3)
MIN_DAYS_FOR_PERFORMANCE = 7


@dataclass
class Finding:
    check: str
    severity: str              # "warning" | "critical"
    message: str
    details: dict = field(default_factory=dict)


def stale_data(data_age_days: int, max_age_days: int = MAX_DATA_AGE_DAYS) -> list[Finding]:
    if data_age_days <= max_age_days:
        return []
    return [Finding("stale_data", "critical",
                    f"forecast uses data {data_age_days} days old (limit {max_age_days})",
                    {"data_age_days": data_age_days})]


def feed_outage(hourly: pd.DataFrame, now: pd.Timestamp, expected_stations: int,
                window: pd.Timedelta = REPORTING_WINDOW) -> list[Finding]:
    recent = hourly[hourly["time"] > now - window]
    reporting = recent["location_id"].nunique()
    if not checks.feed_outage(reporting, expected_stations):
        return []
    return [Finding("feed_outage", "critical",
                    f"only {reporting}/{expected_stations} stations reported in the last "
                    f"{int(window.total_seconds() // 3600)} h",
                    {"reporting": reporting, "expected": expected_stations})]


def stuck_sensors(hourly: pd.DataFrame, now: pd.Timestamp, n: int = checks.STUCK_HOURS,
                  window: pd.Timedelta = REPORTING_WINDOW) -> list[Finding]:
    """Stations whose LATEST reading ends a run of >= n identical hours. Stations that
    stopped reporting are not 'stuck' — that is feed_outage's job (one owner per problem)."""
    out = []
    for loc, g in hourly.groupby("location_id"):
        s = g.set_index("time")["pm25"].sort_index()
        if s.empty or now - s.index[-1] > window:
            continue
        m = checks.stuck_mask(s, n)
        if not m.iloc[-1]:
            continue
        trailing = int((~m[::-1]).cumsum().eq(0).sum())
        out.append(Finding("stuck_sensor", "warning",
                           f"station {loc} has reported {s.iloc[-1]} for {trailing} h",
                           {"location_id": str(loc), "value": float(s.iloc[-1]), "hours": trailing}))
    return out


def model_vs_persistence(y, pred, yesterday, tolerance: float = 1.0,
                         min_days: int = MIN_DAYS_FOR_PERFORMANCE) -> list[Finding]:
    """Recent forecasts scored against what actually happened: is the model still
    beating 'tomorrow = today'? Rows with a missing actual are ignored."""
    y, pred, yesterday = (np.asarray(a, dtype=float) for a in (y, pred, yesterday))
    ok = ~(np.isnan(y) | np.isnan(pred) | np.isnan(yesterday))
    if ok.sum() < min_days:
        return []
    mae_model = float(np.mean(np.abs(pred[ok] - y[ok])))
    mae_persist = float(np.mean(np.abs(yesterday[ok] - y[ok])))
    if mae_model <= mae_persist * tolerance:
        return []
    return [Finding("model_vs_persistence", "warning",
                    f"model MAE {mae_model:.2f} > persistence MAE {mae_persist:.2f} "
                    f"over {int(ok.sum())} recent forecasts",
                    {"mae_model": mae_model, "mae_persistence": mae_persist, "rows": int(ok.sum())})]


def from_performance(perf: dict, **kwargs) -> list[Finding]:
    """model_vs_persistence on the rows the service's /performance returned
    (its model's forecasts for the last 14 days with known actuals)."""
    if not perf.get("rows"):
        return []
    found = model_vs_persistence(perf["y"], perf["pred"], perf["yesterday"], **kwargs)
    for f in found:
        f.details.update({"window": perf.get("window"), "model_version": perf.get("model_version"),
                          "data_version": perf.get("data_version")})
    return found