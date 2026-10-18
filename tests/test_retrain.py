"""Retraining gate: which rows are compared, and when a challenger may replace production."""
import math

import pandas as pd

from src import retrain


def days(start, n, stations=(1, 2)):
    d = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame([{"date": x, "location_id": s} for x in d for s in stations])


def test_window_is_the_last_28_days_and_training_stops_7_days_before_it():
    df = days("2026-08-01", 78)                                   # .. 2026-10-17
    fit_rows, win, start, end = retrain.window(df)
    assert (start, end) == (pd.Timestamp("2026-09-20"), pd.Timestamp("2026-10-17"))
    assert win["date"].nunique() == 28 and win["date"].min() == start
    assert fit_rows["date"].max() == pd.Timestamp("2026-09-12")    # start - 8 days: a 7-day gap
    assert not set(fit_rows["date"]) & set(win["date"])


def metrics(mae, mae_dry=float("nan"), recall=0.0, false_alarm=0.0, bad_days=0):
    return {"mae": mae, "mae_dry": mae_dry, "recall": recall,
            "false_alarm_rate": false_alarm, "bad_days": bad_days}


def test_rainy_window_marks_dry_season_and_recall_not_applicable():
    checks = retrain.persistence_checks(metrics(5.0), metrics(5.5))
    assert checks == {"mae": True, "mae_dry": None, "recall": None, "false_alarm_rate": True}


def test_a_few_isolated_spikes_are_too_few_to_judge_recall():
    # Oct 2026 window: 11 days above 37.5, caught by nobody -> recall 0 vs 0 proves nothing
    few = metrics(4.0, recall=0.0, false_alarm=1.0, bad_days=11)
    assert retrain.persistence_checks(few, metrics(4.2, recall=0.0, false_alarm=1.0, bad_days=11))["recall"] is None
    assert retrain.champion_checks([4.0] * 5, few, few)["recall_not_below_production"] is None


def test_dry_window_judges_all_four_checks():
    checks = retrain.persistence_checks(metrics(5.0, 6.0, 0.8, 0.1, 30),
                                        metrics(5.5, 6.5, 0.7, 0.2, 30))
    assert checks == {"mae": True, "mae_dry": True, "recall": True, "false_alarm_rate": True}


def test_challenger_must_beat_production_by_more_than_seed_noise():
    prod = metrics(5.00)
    just_better = retrain.champion_checks([4.98, 4.97, 4.99, 4.98, 4.96], metrics(4.98), prod)
    assert just_better["mean_mae_beats_production_by_margin"] is False      # 0.02 < margin 0.056
    clearly = retrain.champion_checks([4.90, 4.92, 4.91, 4.89, 4.93], metrics(4.90), prod)
    assert clearly["mean_mae_beats_production_by_margin"] is True
    assert clearly["every_seed_beats_production"] is True
    assert clearly["recall_not_below_production"] is None                   # no bad days


def test_one_unlucky_seed_is_enough_to_reject():
    checks = retrain.champion_checks([4.80, 4.80, 4.80, 4.80, 5.10], metrics(4.80), metrics(5.0))
    assert checks["mean_mae_beats_production_by_margin"] is True
    assert checks["every_seed_beats_production"] is False
    assert retrain.decide(checks) is False


def test_recall_on_unhealthy_days_cannot_get_worse():
    checks = retrain.champion_checks([4.8] * 5, metrics(4.8, recall=0.60, bad_days=40),
                                     metrics(5.0, recall=0.65, bad_days=40))
    assert checks["recall_not_below_production"] is False


def test_decide_needs_no_failures_and_at_least_one_real_check():
    assert retrain.decide({"a": True, "b": None}) is True
    assert retrain.decide({"a": True}, {"b": False}) is False
    assert retrain.decide({"a": None}, {"b": None}) is False     # nothing judged: never pass


def test_clean_turns_nan_into_none_for_json():
    assert retrain.clean({"x": float("nan"), "y": 1.5}) == {"x": None, "y": 1.5}
    assert not any(isinstance(v, float) and math.isnan(v) for v in retrain.clean({"z": math.nan}).values())