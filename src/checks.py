"""Data checks used by both the nightly job and training.

Every threshold comes from docs/data-findings.md, so each number can be defended:
  STUCK_HOURS           Finding 4 — longest healthy run was 7 h; frozen runs were 9 h and up
  MIN_PLAUSIBLE         Finding 2 — whole days at 0.0 are broken sensors, not clean air
  MIN_STATION_FRACTION  Finding 1 — whole-feed outages leave fewer than half the stations
  DISAGREE_*            Finding 9 — over 33 months no station disagreed with its neighbours
                        (x2 / x0.5 and >= 15 ug/m3 apart) for 24 h in a row; longest was 22 h
"""
import pandas as pd

STUCK_HOURS = 8
MIN_PLAUSIBLE = 1.0
MIN_STATION_FRACTION = 0.5
NEIGHBOUR_RADIUS_KM = 10.0     # neighbours = stations within 10 km (77 of 81 have >= 3)
MIN_NEIGHBOURS = 3             # fewer reporting neighbours: the station cannot be judged
DISAGREE_LOW = 0.5             # below half the neighbour median ...
DISAGREE_HIGH = 2.0            # ... or above twice it
DISAGREE_MIN_GAP = 15.0        # and at least 15 ug/m3 apart (small values differ by ratio easily)
DISAGREE_HOURS = 24            # for 24 h in a row


def stuck_mask(hourly: pd.Series, n: int = STUCK_HOURS) -> pd.Series:
    """True for every hour in a run of >= n identical consecutive readings.

    `hourly` is one station's readings, indexed by timestamp. A missing hour breaks
    a run: we can't claim a sensor was frozen through an hour we have no data for.
    """
    s = hourly.sort_index()
    new_run = (s != s.shift()) | (s.index.to_series().diff() != pd.Timedelta(hours=1))
    run_id = new_run.cumsum()
    run_len = s.groupby(run_id.values).transform("size")
    return pd.Series(run_len.values >= n, index=s.index)


def implausible_mask(daily: pd.Series, min_value: float = MIN_PLAUSIBLE) -> pd.Series:
    """True for daily averages too low to be real air."""
    return daily < min_value


def feed_outage(stations_reporting: int, stations_expected: int,
                min_fraction: float = MIN_STATION_FRACTION) -> bool:
    """True when too few stations reported: the feed broke, not individual sensors."""
    return stations_reporting < stations_expected * min_fraction