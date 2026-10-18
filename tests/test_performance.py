"""/performance: the serving model's recent forecasts vs persistence, and the monitor check on them."""
from dataclasses import replace

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from src import monitor
from src.service import Loaded, create_app


class FakeModel:
    feature_names_in_ = ["lag1"]

    def __init__(self, change=0.0):
        self.change = change

    def predict(self, X):
        return np.full(len(X), self.change)


def recent(n_days=14, stations=(135, 136)):
    d = pd.date_range("2026-10-03", periods=n_days, freq="D")
    return pd.DataFrame([{"date": x, "location_id": s, "lag1": 20.0, "y": 22.0} for x in d for s in stations])


def loaded(model=None, rows_recent=None) -> Loaded:
    rows = pd.DataFrame({"location_id": [135, 136], "lag1": [20.0, 21.0]})
    rows.index = rows["location_id"].astype(str)
    return Loaded(model or FakeModel(), "delta", 0.25, "pm25-forecast", "1", rows,
                  pd.Timestamp("2026-10-18"), "aaaa", "wwww", "manifest", rows_recent)


def test_performance_reports_model_and_persistence_on_the_same_rows():
    # model says +4 per day, shrink 0.25 -> forecast 21; truth 22; persistence says 20
    with TestClient(create_app(lambda: loaded(FakeModel(4.0), recent()), background=False)) as c:
        p = c.get("/performance").json()
    assert p["rows"] == 28 and p["window"] == ["2026-10-03", "2026-10-16"]
    assert (p["mae_model"], p["mae_persistence"]) == (1.0, 2.0)
    assert len(p["y"]) == len(p["pred"]) == len(p["yesterday"]) == 28
    assert monitor.from_performance(p) == []                       # model beats persistence


def test_monitor_warns_when_the_model_loses_to_persistence():
    # model says -8 -> forecast 18; truth 22 -> error 4 vs persistence 2
    with TestClient(create_app(lambda: loaded(FakeModel(-8.0), recent()), background=False)) as c:
        p = c.get("/performance").json()
    [f] = monitor.from_performance(p)
    assert f.check == "model_vs_persistence" and f.severity == "warning"
    assert f.details["window"] == ["2026-10-03", "2026-10-16"] and f.details["model_version"] == "1"


def test_no_recent_rows_means_no_rows_and_no_finding():
    with TestClient(create_app(lambda: loaded(), background=False)) as c:
        p = c.get("/performance").json()
    assert p["rows"] == 0 and monitor.from_performance(p) == []


def test_a_failing_backtest_never_stops_forecasting():
    class Broken(FakeModel):
        calls = 0

        def predict(self, X):
            Broken.calls += 1
            if Broken.calls == 1:                  # the backtest is the first call
                raise ValueError("boom")
            return np.zeros(len(X))

    with TestClient(create_app(lambda: loaded(Broken(), recent()), background=False)) as c:
        assert c.get("/ready").status_code == 200
        assert c.get("/performance").json()["rows"] == 0
        assert c.post("/predict", json={"station_id": "135"}).status_code == 200


def test_performance_is_not_logged_as_traffic(capsys):
    with TestClient(create_app(lambda: loaded(FakeModel(), recent()), background=False)) as c:
        c.get("/performance")
    assert '"path": "/performance"' not in capsys.readouterr().out


def test_reload_recomputes_the_backtest_for_the_new_data():
    newer = recent().assign(date=lambda d: d["date"] + pd.Timedelta(days=1))
    app = create_app(lambda: loaded(FakeModel(4.0), recent()), background=False,
                     data_check=lambda m: replace(m, recent=newer, data_version="bbbb"))
    with TestClient(app) as c:
        assert c.get("/performance").json()["window"][1] == "2026-10-16"
        assert app.state.check_data_now() is True
        p = c.get("/performance").json()
    assert p["window"][1] == "2026-10-17" and p["data_version"] == "bbbb" and p["rows"] == 28