"""Metrics and the evaluation gate: one definition, used by training, the pipeline and tests.

Gate (decided before the first run), against persistence on the SAME rows:
  mae < persistence · mae_dry < persistence · recall >= persistence · false alarms <= persistence
"""
import numpy as np

THRESHOLD = 37.5                     # Thai 24-h PM2.5 standard, µg/m³
DRY_MONTHS = (11, 12, 1, 2, 3, 4)


def metrics(y, pred, yesterday, month) -> dict:
    y, pred, yesterday = map(np.asarray, (y, pred, yesterday))
    err = np.abs(y - pred)
    dry = np.isin(month, DRY_MONTHS)
    bad, alert = y > THRESHOLD, pred > THRESHOLD
    onset = bad & (yesterday <= THRESHOLD)
    return {
        "rows": int(len(y)),
        "mae": round(float(err.mean()), 3),
        "mae_dry": round(float(err[dry].mean()), 3) if dry.any() else float("nan"),
        "mae_rainy": round(float(err[~dry].mean()), 3) if (~dry).any() else float("nan"),
        "bias": round(float((pred - y).mean()), 3),
        "bad_days": int(bad.sum()),
        "recall": round(float((bad & alert).sum() / max(bad.sum(), 1)), 3),
        "false_alarm_rate": round(float((alert & ~bad).sum() / max(alert.sum(), 1)), 3),
        "onsets": int(onset.sum()),
        "onset_recall": round(float((onset & alert).sum() / max(onset.sum(), 1)), 3),
    }


def gate(model: dict, base: dict) -> dict:
    return {
        "mae": bool(model["mae"] < base["mae"]),
        "mae_dry": bool(model["mae_dry"] < base["mae_dry"]),
        "recall": bool(model["recall"] >= base["recall"]),
        "false_alarm_rate": bool(model["false_alarm_rate"] <= base["false_alarm_rate"]),
    }