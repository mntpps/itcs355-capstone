import numpy as np
import pandas as pd

from src import split

DAYS = pd.Series(pd.date_range("2024-01-02", "2026-10-11"))
LAB = split.assign(DAYS)


def closest(a, b) -> int:
    return int(np.abs(a.values[:, None] - b.values[None, :]).min() / np.timedelta64(1, "D"))


def test_no_split_is_within_a_week_of_another():
    train, val, test = (DAYS[LAB == k] for k in ("train", "val", "test"))
    assert closest(train, val) > 7
    assert closest(train, test) > 7
    assert closest(val, test) > 7


def test_validation_contains_a_dry_season():
    months = set(DAYS[LAB == "val"].dt.month)
    assert {12, 1, 2} <= months


def test_final_model_never_sees_the_test_year():
    seen = DAYS[LAB.isin(["train", "val"])]
    assert seen.max() < split.TEST[0] - split.GAP