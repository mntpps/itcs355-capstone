"""Next-day PM2.5 forecast service.

Loads the production model ONCE (the version named in config/production.json,
downloaded from the registry by version) plus the latest PM2.5 data, then builds
features and forecasts with the SAME code as training (src.features, src.forecast).
Callers send only station ids, so serving can never compute features differently
from training (no training/serving skew).

    uvicorn src.service:app --port 8000
"""
from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src import config, features, forecast
from src.evaluate import THRESHOLD

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = REPO_ROOT / "config" / "production.json"
log = logging.getLogger("pm25.service")


@dataclass
class Loaded:
    model: object
    target_mode: str
    shrink: float
    model_name: str
    model_version: str
    rows: pd.DataFrame          # one feature row per station for forecast_date; index = station id (str)
    forecast_date: pd.Timestamp


def load_production() -> Loaded:
    """Same steps as scripts/reload_check.py, with the version taken from production.json."""
    import mlflow
    import mlflow.sklearn
    from cloudlayer.factory import get_adapter

    cfg = config.load()
    prod = json.loads(PRODUCTION.read_text())
    name, version = prod["model"], str(prod["version"])
    ml = get_adapter(cfg)._ml_client()
    with tempfile.TemporaryDirectory() as tmp:
        ml.models.download(name=name, version=version, download_path=tmp)
        mlmodel = next(Path(tmp).rglob("MLmodel"))
        model = mlflow.sklearn.load_model(str(mlmodel.parent))
        meta = mlflow.models.Model.load(str(mlmodel)).metadata or {}

    df = features.build(pd.read_csv(cfg.targets_path), pd.read_csv(cfg.weather_daily_path),
                        pd.read_csv(cfg.stations_path))
    day = df["date"].max()
    rows = df[df["date"] == day].copy()
    rows.index = rows["location_id"].astype(str)
    return Loaded(model, meta["target_mode"], float(meta.get("shrink", 1.0)),
                  name, version, rows, day)


class PredictRequest(BaseModel):
    station_id: str = Field(min_length=1, max_length=20, examples=["135"])


class BatchRequest(BaseModel):
    station_ids: list[str] | None = Field(
        default=None, max_length=200, description="Station ids to forecast; omit for every station.")


def create_app(loader: Callable[[], Loaded] = load_production, background: bool = True) -> FastAPI:
    """loader is injectable so tests can use a fake model without Azure."""
    state: dict = {"loaded": None, "error": None}

    def _load() -> None:
        t0 = time.perf_counter()
        try:
            m = loader()
            state["loaded"] = m
            log.warning("ready: %s v%s, forecast_date %s, %d stations, loaded in %.1fs",
                        m.model_name, m.model_version, f"{m.forecast_date:%Y-%m-%d}",
                        len(m.rows), time.perf_counter() - t0)
        except Exception as exc:  # reported through /ready; the process stays up but gets no traffic
            state["error"] = f"{type(exc).__name__}: {exc}"
            log.exception("model load failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if background:   # /health answers immediately; /ready turns 200 when loading finishes
            threading.Thread(target=_load, daemon=True).start()
        else:
            _load()
        yield

    app = FastAPI(title="Bangkok PM2.5 next-day forecast", lifespan=lifespan)

    def _need() -> Loaded:
        if state["loaded"] is None:
            raise HTTPException(503, detail=state["error"] or "model not loaded yet")
        return state["loaded"]

    def _forecast(m: Loaded, ids: list[str]) -> list[dict]:
        rows = m.rows.loc[ids]
        preds = forecast.predict(m.model, rows, m.target_mode, m.shrink)
        return [{"station_id": sid,
                 "forecast_date": f"{m.forecast_date:%Y-%m-%d}",
                 "pm25_forecast": round(float(p), 1),
                 "yesterday_pm25": round(float(lag1), 1),
                 "unhealthy": bool(p > THRESHOLD),
                 "threshold": THRESHOLD}
                for sid, lag1, p in zip(ids, rows["lag1"].to_numpy(), preds)]

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        m = state["loaded"]
        if m is None:
            return JSONResponse(status_code=503, content={"ready": False, "error": state["error"]})
        last_obs = (m.forecast_date - pd.Timedelta(days=1)).date()
        today = pd.Timestamp.now(tz="Asia/Bangkok").date()
        return {"ready": True, "model": m.model_name, "model_version": m.model_version,
                "forecast_date": f"{m.forecast_date:%Y-%m-%d}",
                "last_observation_date": str(last_obs),
                "data_age_days": (today - last_obs).days,
                "stations": len(m.rows)}

    @app.post("/predict")
    def predict(req: PredictRequest):
        m = _need()
        if req.station_id not in m.rows.index:
            raise HTTPException(404, detail=f"unknown station_id {req.station_id!r}")
        return {**_forecast(m, [req.station_id])[0],
                "model": m.model_name, "model_version": m.model_version}

    @app.post("/predict/batch")
    def predict_batch(req: BatchRequest):
        m = _need()
        ids = list(m.rows.index) if req.station_ids is None else list(dict.fromkeys(req.station_ids))
        known = [s for s in ids if s in m.rows.index]
        return {"model": m.model_name, "model_version": m.model_version,
                "forecast_date": f"{m.forecast_date:%Y-%m-%d}",
                "forecasts": _forecast(m, known) if known else [],
                "unknown_station_ids": [s for s in ids if s not in m.rows.index]}

    return app


app = create_app()