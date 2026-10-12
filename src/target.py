"""The daily value the model predicts (docs/data-findings.md, Finding 7).

Air4Thai's "hourly" PM2.5 is a 24-hour moving average, so the reading at the end of
day D already is PCD's own 24-h average for day D.

  END_OF_DAY_HOURS  23h first; 22h costs median 0.20, p95 1.10 µg/m³.
                    21h rejected: p95 2.00, not under the 2 µg/m³ limit set beforehand.
  CONTAMINATION     A frozen sensor makes the moving average slide toward the frozen value
                    for 24 h before it goes flat, and back out for 24 h after. Readings within
                    24 h of a stuck hour are not trusted.
"""
import numpy as np
import pandas as pd

from src import checks

TZ = "Asia/Bangkok"
END_OF_DAY_HOURS = (23, 22)
CONTAMINATION = pd.Timedelta(hours=24)


def near_flagged(mask: pd.Series, window: pd.Timedelta = CONTAMINATION) -> pd.Series:
    """True for every timestamp within `window` of a flagged one (either side)."""
    t = mask.index.values
    flagged = t[mask.values]
    if len(flagged) == 0:
        return pd.Series(False, index=mask.index)
    pos = np.searchsorted(flagged, t)
    before = flagged[np.clip(pos - 1, 0, len(flagged) - 1)]
    after = flagged[np.clip(pos, 0, len(flagged) - 1)]
    w = np.timedelta64(window)
    near = (np.abs(t - before) <= w) | (np.abs(after - t) <= w)
    return pd.Series(near, index=mask.index)


def daily_target(hourly: pd.Series) -> pd.DataFrame:
    """One station's readings (tz-aware index) -> columns date, pm25, source."""
    s = hourly.sort_index().tz_convert(TZ)
    bad = near_flagged(checks.stuck_mask(s))
    s = s[~bad.values]
    df = pd.DataFrame({"pm25": s.values, "date": s.index.date, "hour": s.index.hour})

    rows = []
    for day, g in df.groupby("date"):
        for hr in END_OF_DAY_HOURS:
            hit = g.loc[g["hour"] == hr, "pm25"]
            if len(hit):
                rows.append({"date": day, "pm25": float(hit.iloc[0]), "source": f"{hr:02d}h"})
                break
    out = pd.DataFrame(rows, columns=["date", "pm25", "source"])
    out = out[~checks.implausible_mask(out["pm25"])]
    return out.reset_index(drop=True)