import threading
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.service import Loaded, create_app


class FakeModel:
    """Predicts a change of +4.0 for every row and uses only lag1."""
    feature_names_in_ = np.array(["lag1"])

    def predict(self, X):
        return np.full(len(X), 4.0)


def fake_loaded():
    rows = pd.DataFrame({"location_id": [135, 200], "lag1": [20.0, 40.0]})
    rows.index = rows["location_id"].astype(str)
    return Loaded(FakeModel(), "delta", 0.25, "pm25-forecast", "1", rows, pd.Timestamp("2026-10-12"))


@pytest.fixture
def client():
    with TestClient(create_app(fake_loaded, background=False)) as c:
        yield c


def test_predict_uses_the_training_forecast_rule(client):
    body = client.post("/predict", json={"station_id": "135"}).json()
    assert body["pm25_forecast"] == 21.0          # 20 + 0.25 * 4: forecast.predict's delta + shrink rule
    assert body["yesterday_pm25"] == 20.0
    assert body["model_version"] == "1"
    assert body["forecast_date"] == "2026-10-12"


def test_unhealthy_flag_uses_the_37_5_threshold(client):
    body = client.post("/predict", json={"station_id": "200"}).json()
    assert body["pm25_forecast"] == 41.0 and body["unhealthy"] is True


def test_unknown_station_is_404(client):
    assert client.post("/predict", json={"station_id": "999"}).status_code == 404


def test_malformed_body_is_422(client):
    assert client.post("/predict", json={"station": "135"}).status_code == 422


def test_batch_all_stations_and_unknown_ids(client):
    body = client.post("/predict/batch", json={}).json()
    assert {f["station_id"] for f in body["forecasts"]} == {"135", "200"}
    body = client.post("/predict/batch", json={"station_ids": ["200", "999"]}).json()
    assert [f["station_id"] for f in body["forecasts"]] == ["200"]
    assert body["unknown_station_ids"] == ["999"]


def test_alive_but_not_ready_until_model_loaded():
    release = threading.Event()

    def slow_loader():
        release.wait(5)
        return fake_loaded()

    with TestClient(create_app(slow_loader, background=True)) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/ready").status_code == 503
        assert c.post("/predict", json={"station_id": "135"}).status_code == 503
        release.set()
        for _ in range(50):
            if c.get("/ready").status_code == 200:
                break
            time.sleep(0.05)
        assert c.get("/ready").status_code == 200


def test_failed_load_stays_alive_but_not_ready():
    def broken():
        raise RuntimeError("registry unreachable")

    with TestClient(create_app(broken, background=False)) as c:
        r = c.get("/ready")
        assert r.status_code == 503 and "registry unreachable" in r.json()["error"]
        assert c.get("/health").status_code == 200


def test_responses_name_the_revision_that_served_them(client, monkeypatch):
    monkeypatch.setenv("CONTAINER_APP_REVISION", "itcs-forecast--m1-abc")
    assert client.post("/predict", json={"station_id": "135"}).json()["revision"] == "itcs-forecast--m1-abc"
    assert client.get("/ready").json()["revision"] == "itcs-forecast--m1-abc"