"""Time-based split. Never random: neighbouring days are near-copies of each other.

  train       2024-01-02 .. 2024-10-24  and  2025-05-08 .. 2025-09-23
  validation  2024-11-01 .. 2025-04-30   (the 2024-25 dry season; chooses settings)
  test        2025-10-01 .. 2026-09-30   (a full year incl. the 2025-26 dry season)
7-day gaps around validation and before test: the lag and 7-day-mean inputs reach back
7 days, so a gap of 7 keeps one split's target days out of another split's inputs.

Compromise, stated: some training days come after validation. Validation only chooses
settings; the test year stays strictly after everything the final model was fitted on.
"""
import pandas as pd

GAP = pd.Timedelta(days=7)
VAL = (pd.Timestamp("2024-11-01"), pd.Timestamp("2025-04-30"))
TEST = (pd.Timestamp("2025-10-01"), pd.Timestamp("2026-09-30"))


def assign(dates: pd.Series) -> pd.Series:
    """Label each date 'train', 'val', 'test' or 'unused' (gaps, and days after test)."""
    d = pd.to_datetime(pd.Series(dates))
    label = pd.Series("train", index=d.index)
    near_val = ((d >= VAL[0] - GAP) & (d < VAL[0])) | ((d > VAL[1]) & (d <= VAL[1] + GAP))
    near_test = (d >= TEST[0] - GAP) & (d < TEST[0])
    label[near_val | near_test | (d > TEST[1])] = "unused"
    label[(d >= VAL[0]) & (d <= VAL[1])] = "val"
    label[(d >= TEST[0]) & (d <= TEST[1])] = "test"
    return label