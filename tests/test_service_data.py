"""The service picks up a new daily refresh without restarting or reloading the model."""
from dataclasses import replace

import pandas as pd
import numpy as np
from fastapi.testclient import TestClient

from src import service
from src.service import Loaded, create_app


class FakeModel:
    """What src.forecast.predict needs from a model: feature names and predict()."""
    feature_names_in_ = ["lag1"]

    def predict(self, X):
        return np.zeros(len(X))


def rows(day_lag1: float) -> pd.DataFrame:
    r = pd.DataFrame({"location_id": [135, 136], "lag1": [day_lag1, day_lag1 + 1]})
    r.index = r["location_id"].astype(str)
    return r


def old_data() -> Loaded:
    return Loaded(FakeModel(), "delta", 0.25, "pm25-forecast", "1", rows(10.0),
                  pd.Timestamp("2026-10-12"), "aaaa", "wwww", "pointer")


def new_data(m: Loaded) -> Loaded:
    return replace(m, rows=rows(20.0), forecast_date=pd.Timestamp("2026-10-16"),
                   data_version="bbbb", weather_version="xxxx", data_from="manifest")


def test_new_manifest_is_swapped_in_and_the_model_is_kept(capsys):
    app = create_app(old_data, background=False, data_check=new_data)
    with TestClient(app) as c:
        before = c.get("/ready").json()
        model_before = c.post("/predict", json={"station_id": "135"}).json()
        assert app.state.check_data_now() is True
        after = c.get("/ready").json()
        model_after = c.post("/predict", json={"station_id": "135"}).json()
    assert (before["data_version"], before["data_from"]) == ("aaaa", "pointer")
    assert (after["data_version"], after["data_from"]) == ("bbbb", "manifest")
    assert after["forecast_date"] == "2026-10-16"
    assert model_before["yesterday_pm25"] == 10.0 and model_after["yesterday_pm25"] == 20.0
    assert model_after["model_version"] == model_before["model_version"]
    assert '"event": "data_reload"' in capsys.readouterr().out


def test_no_change_keeps_serving_the_same_data():
    app = create_app(old_data, background=False, data_check=lambda m: None)
    with TestClient(app) as c:
        assert app.state.check_data_now() is False
        assert c.get("/ready").json()["data_version"] == "aaaa"


def test_failed_check_keeps_old_data_and_reports_it():
    def broken(m):
        raise ConnectionError("blob storage unreachable")

    app = create_app(old_data, background=False, data_check=broken)
    with TestClient(app) as c:
        assert app.state.check_data_now() is False
        r = c.get("/ready")
        assert r.status_code == 200                       # still serving yesterday's data
        assert r.json()["data_version"] == "aaaa"
        assert "unreachable" in r.json()["data_check_error"]
        assert c.post("/predict", json={"station_id": "135"}).status_code == 200


def test_check_data_does_nothing_locally(monkeypatch):
    monkeypatch.delenv("BLOB_URI", raising=False)
    assert service.check_data(old_data()) is None


def test_check_data_skips_reload_when_manifest_names_the_same_version(monkeypatch):
    monkeypatch.setenv("BLOB_URI", "https://x/c")
    monkeypatch.setattr(service.data_source, "serving_versions", lambda names, uri: (
        {"targets.csv": "aaaa", "weather_daily.csv": "wwww"}, "manifest"))
    monkeypatch.setattr(service, "load_data", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert service.check_data(old_data()) is None


def test_check_data_reloads_only_data_when_version_changed(monkeypatch):
    monkeypatch.setenv("BLOB_URI", "https://x/c")
    monkeypatch.setattr(service.data_source, "serving_versions", lambda names, uri: (
        {"targets.csv": "bbbb", "weather_daily.csv": "wwww"}, "manifest"))
    monkeypatch.setattr(service.config, "load", lambda: None)
    seen = {}

    def fake_load_data(cfg, uri, want, source):
        seen.update(want=want, source=source)
        return {"rows": rows(20.0), "forecast_date": pd.Timestamp("2026-10-16"),
                "data_version": want["targets.csv"], "weather_version": want["weather_daily.csv"],
                "data_from": source}

    monkeypatch.setattr(service, "load_data", fake_load_data)
    m = old_data()
    new = service.check_data(m)
    assert seen == {"want": {"targets.csv": "bbbb", "weather_daily.csv": "wwww"}, "source": "manifest"}
    assert new.model is m.model and new.data_version == "bbbb" and new.data_from == "manifest"