"""Turn model output into a PM2.5 forecast.

Used by training, the nightly job and the API, so all three agree on what the model's
number means. target_mode 'level': the model predicts tomorrow's value. 'delta': it
predicts the change from yesterday, so forecast = yesterday + change.
"""
import numpy as np
import pandas as pd

TARGET_MODES = ("level", "delta")


def predict(model, rows: pd.DataFrame, target_mode: str, shrink: float = 1.0) -> np.ndarray:
    """shrink scales the predicted change: 0 = persistence, 1 = the model's full change."""
    if target_mode not in TARGET_MODES:
        raise ValueError(f"target_mode must be one of {TARGET_MODES}, got {target_mode!r}")
    out = model.predict(rows[list(model.feature_names_in_)])
    if target_mode == "delta":
        out = rows["lag1"].to_numpy() + shrink * out
    elif shrink != 1.0:
        raise ValueError("shrink only applies to target_mode='delta'")
    return np.clip(out, 0, None)          # PM2.5 cannot be negative