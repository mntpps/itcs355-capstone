import numpy as np

from src import evaluate

BASE = dict(mae=4.0, mae_dry=5.0, recall=0.6, false_alarm_rate=0.3)


def test_gate_fails_when_model_is_worse_overall():
    checks = evaluate.gate(dict(BASE, mae=4.5), BASE)
    assert checks["mae"] is False
    assert not all(checks.values())


def test_gate_fails_on_more_false_alarms_even_if_recall_improves():
    checks = evaluate.gate(dict(BASE, mae=3.9, mae_dry=4.9, recall=0.8, false_alarm_rate=0.5), BASE)
    assert checks["recall"] is True
    assert checks["false_alarm_rate"] is False


def test_gate_passes_when_better_everywhere():
    assert all(evaluate.gate(dict(BASE, mae=3.9, mae_dry=4.9), BASE).values())


def test_alert_counting():
    y = np.array([40, 40, 10, 10])
    pred = np.array([40, 10, 40, 10])
    yesterday = np.array([10, 40, 10, 10])
    m = evaluate.metrics(y, pred, yesterday, np.ones(4))
    assert m["recall"] == 0.5             # 1 of 2 bad days alerted
    assert m["false_alarm_rate"] == 0.5   # 1 of 2 alerts wrong
    assert m["onsets"] == 1 and m["onset_recall"] == 1.0