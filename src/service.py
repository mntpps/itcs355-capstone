"""Next-day PM2.5 forecast service.

Loads the production model ONCE (the version named in config/production.json,
downloaded from the registry by version) plus the latest PM2.5 data, then builds
features and forecasts with the SAME code as training (src.features, src.forecast).
Callers send only station ids, so serving can never compute features differently
from training (no training/serving skew).

Data: the version named in the serving manifest written by the daily refresh
(src/refresh.py), or the committed DVC pointers if there is no manifest yet. A
background check re-reads the manifest every DATA_CHECK_SECONDS (default 3600)
and, when the version changed, rebuilds the feature rows and swaps them in. The
model is never reloaded by this check.

    uvicorn src.service:app --port 8000
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src import config, data_source, features, forecast
from src.evaluate import THRESHOLD


REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = REPO_ROOT / "config" / "production.json"
DATA_FILES = ("targets.csv", "weather_daily.csv")
PERFORMANCE_DAYS = 14       # /performance: the last 14 days that have a known next-day value
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
    data_version: str = "unknown"       # md5 of targets.csv
    weather_version: str = "unknown"    # md5 of weather_daily.csv
    data_from: str = "pointer"          # "manifest" (daily refresh) or "pointer" (committed .dvc)
    recent: pd.DataFrame | None = None  # last days with a known actual; with_backtest adds "pred"


def load_data(cfg, blob_uri: str | None, want: dict | None = None, source: str = "pointer") -> dict:
    """Make sure the serving files hold version `want`, then build the latest feature rows."""
    if want is None:
        want, source = data_source.serving_versions(DATA_FILES, blob_uri)
    paths = {"targets.csv": cfg.targets_path, "weather_daily.csv": cfg.weather_daily_path}
    for n in DATA_FILES:
        data_source.ensure(paths[n], blob_uri, want=want[n])
    df = features.build(pd.read_csv(cfg.targets_path), pd.read_csv(cfg.weather_daily_path),
                        pd.read_csv(cfg.stations_path))
    day = df["date"].max()
    rows = df[df["date"] == day].copy()
    rows.index = rows["location_id"].astype(str)
    known = df[df["y"].notna()]
    recent = known[known["date"] > known["date"].max() - pd.Timedelta(days=PERFORMANCE_DAYS)].copy()
    return {"rows": rows, "forecast_date": day, "data_version": want["targets.csv"],
            "weather_version": want["weather_daily.csv"], "data_from": source, "recent": recent}


def with_backtest(m: Loaded) -> Loaded:
    """Add the serving model's forecasts for the recent rows: what it would have said each day.
    Never fatal: if it fails, /performance has no rows and forecasting is unaffected."""
    if m.recent is None or m.recent.empty or "pred" in m.recent:
        return m
    try:
        out = m.recent[["date", "location_id", "y", "lag1"]].copy()
        out["pred"] = forecast.predict(m.model, m.recent, m.target_mode, m.shrink)
        return replace(m, recent=out)
    except Exception:  # noqa: BLE001
        log.exception("backtest failed; /performance will report no rows")
        return replace(m, recent=None)


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

    data = load_data(cfg, os.environ.get("BLOB_URI"))
    return Loaded(model, meta["target_mode"], float(meta.get("shrink", 1.0)), name, version, **data)


def check_data(m: Loaded) -> Loaded | None:
    """New data if the manifest names a different version than `m` serves, else None."""
    blob_uri = os.environ.get("BLOB_URI")
    if not blob_uri:
        return None                     # local run: data/ only changes when you change it
    want, source = data_source.serving_versions(DATA_FILES, blob_uri)
    if (want["targets.csv"], want["weather_daily.csv"]) == (m.data_version, m.weather_version):
        return None
    return replace(m, **load_data(config.load(), blob_uri, want, source))


class PredictRequest(BaseModel):
    station_id: str = Field(min_length=1, max_length=20, examples=["135"])


class BatchRequest(BaseModel):
    station_ids: list[str] | None = Field(
        default=None, max_length=200, description="Station ids to forecast; omit for every station.")


def _revision() -> str:
    return os.environ.get("CONTAINER_APP_REVISION", "local")


def emit(event: str, **fields) -> None:
    """One JSON line per event on stdout; Container Apps ships stdout to Log Analytics."""
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                      "event": event, **fields}), flush=True)


def create_app(loader: Callable[[], Loaded] = load_production, background: bool = True,
               data_check: Callable[[Loaded], Loaded | None] | None = check_data,
               check_every: float | None = None) -> FastAPI:
    """loader and data_check are injectable so tests can use fakes without Azure."""
    state: dict = {"loaded": None, "error": None, "data_error": None}
    if check_every is None:
        check_every = float(os.environ.get("DATA_CHECK_SECONDS", "3600"))

    def _load() -> None:
        t0 = time.perf_counter()
        try:
            m = with_backtest(loader())
            state["loaded"] = m
            log.warning("ready: %s v%s, forecast_date %s, %d stations, data from %s, loaded in %.1fs",
                        m.model_name, m.model_version, f"{m.forecast_date:%Y-%m-%d}",
                        len(m.rows), m.data_from, time.perf_counter() - t0)
        except Exception as exc:  # reported through /ready; the process stays up but gets no traffic
            state["error"] = f"{type(exc).__name__}: {exc}"
            log.exception("model load failed")

    def _check() -> bool:
        """One data check. True if new data was swapped in. A failure keeps serving the old data."""
        m = state["loaded"]
        if m is None or data_check is None:
            return False
        try:
            new = data_check(m)
        except Exception as exc:  # old data stays; the freshness monitor will notice
            state["data_error"] = f"{type(exc).__name__}: {exc}"
            log.exception("data check failed")
            emit("data_reload_failed", error=state["data_error"], revision=_revision())
            return False
        state["data_error"] = None
        if new is None:
            return False
        new = with_backtest(new)
        state["loaded"] = new           # one assignment: a request sees all old or all new rows
        emit("data_reload", data_from=new.data_from,
             old_data_version=m.data_version, new_data_version=new.data_version,
             old_forecast_date=f"{m.forecast_date:%Y-%m-%d}",
             new_forecast_date=f"{new.forecast_date:%Y-%m-%d}",
             stations=len(new.rows), revision=_revision())
        return True

    def _watch() -> None:
        while True:
            time.sleep(check_every)
            _check()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if background:   # /health answers immediately; /ready turns 200 when loading finishes
            threading.Thread(target=_load, daemon=True).start()
            if data_check is not None and check_every > 0:
                threading.Thread(target=_watch, daemon=True).start()
        else:
            _load()
        yield

    app = FastAPI(title="Bangkok PM2.5 next-day forecast", lifespan=lifespan)
    app.state.check_data_now = _check   # tests (and a future admin hook) can run one check

    @app.middleware("http")
    async def log_and_tag(request, call_next):
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # unhandled bug: still answer, still attributable to this revision
            log.exception("unhandled error on %s", request.url.path)
            response = JSONResponse(status_code=500, content={"detail": "internal error"})
        response.headers["x-revision"] = _revision()
        if request.url.path not in ("/health", "/ready", "/performance"):  # probes/monitor; not traffic
            m = state["loaded"]
            emit("request", method=request.method, path=request.url.path,
                 status=response.status_code,
                 latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                 revision=_revision(), model_version=m.model_version if m else None)
        return response

    def _need() -> Loaded:
        if state["loaded"] is None:
            raise HTTPException(503, detail=state["error"] or "model not loaded yet")
        return state["loaded"]

    def _forecast(m: Loaded, ids: list[str]) -> list[dict]:
        rows = m.rows.loc[ids]
        preds = forecast.predict(m.model, rows, m.target_mode, m.shrink)
        out = [{"station_id": sid,
                "forecast_date": f"{m.forecast_date:%Y-%m-%d}",
                "pm25_forecast": round(float(p), 1),
                "yesterday_pm25": round(float(lag1), 1),
                "unhealthy": bool(p > THRESHOLD),
                "threshold": THRESHOLD}
               for sid, lag1, p in zip(ids, rows["lag1"].to_numpy(), preds)]
        for o in out:
            emit("prediction", station_id=o["station_id"], forecast_date=o["forecast_date"],
                 pm25_forecast=o["pm25_forecast"], yesterday_pm25=o["yesterday_pm25"],
                 model_version=m.model_version, data_version=m.data_version, revision=_revision())
        return out

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
                "stations": len(m.rows),
                "data_version": m.data_version,
                "data_from": m.data_from,
                "data_check_error": state["data_error"],
                "revision": _revision(),
                "image": os.environ.get("IMAGE_DIGEST", "local")}


    @app.get("/performance")
    def performance():
        """The serving model on the last PERFORMANCE_DAYS days with known actuals, next to
        persistence ('tomorrow = today'). The monitor turns this into a finding."""
        m = state["loaded"]
        if m is None:
            return JSONResponse(status_code=503, content={"ready": False, "error": state["error"]})
        b = m.recent
        if b is None or "pred" not in b:
            return {"model_version": m.model_version, "data_version": m.data_version, "rows": 0}
        return {"model": m.model_name, "model_version": m.model_version,
                "data_version": m.data_version,
                "window": [f"{b['date'].min():%Y-%m-%d}", f"{b['date'].max():%Y-%m-%d}"],
                "rows": len(b),
                "mae_model": round(float((b["pred"] - b["y"]).abs().mean()), 3),
                "mae_persistence": round(float((b["lag1"] - b["y"]).abs().mean()), 3),
                "y": b["y"].round(3).tolist(), "pred": b["pred"].round(3).tolist(),
                "yesterday": b["lag1"].round(3).tolist()}
    

    @app.post("/predict")
    def predict(req: PredictRequest):
        m = _need()
        if req.station_id not in m.rows.index:
            raise HTTPException(404, detail=f"unknown station_id {req.station_id!r}")
        return {**_forecast(m, [req.station_id])[0],
                "model": m.model_name, "model_version": m.model_version,
                "revision": _revision()}

    @app.post("/predict/batch")
    def predict_batch(req: BatchRequest):
        m = _need()
        ids = list(m.rows.index) if req.station_ids is None else list(dict.fromkeys(req.station_ids))
        known = [s for s in ids if s in m.rows.index]
        return {"model": m.model_name, "model_version": m.model_version,
                "revision": _revision(),
                "forecast_date": f"{m.forecast_date:%Y-%m-%d}",
                "forecasts": _forecast(m, known) if known else [],
                "unknown_station_ids": [s for s in ids if s not in m.rows.index]}

    return app


app = create_app()