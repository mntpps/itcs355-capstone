"""Weekly retraining: train a challenger in Azure ML, compare it with PRODUCTION on the
same recent window, and register it as `staging` only if it passes the gate.

    python scripts/retrain.py --image <registry>/itcs355@sha256:...
    python scripts/retrain.py --image ... --no-register     # run and judge, register nothing

Promotion is NOT automatic: a staging model reaches production only through
scripts/promote.py -> pull request -> CI -> canary, exactly like v1.

Gate (all must hold; checks the window cannot judge are reported n/a):
  vs persistence  the four checks of src/evaluate.py, on the window
  vs production   mean MAE over 5 seeds <= production MAE - 2 x seed SD (0.056)
                  every seed's MAE < production MAE
                  recall on days > 37.5 not below production's
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
# production is scored on its own copy of the data, never on your working data/ folder
os.environ["DATA_DIR"] = str(REPO_ROOT / "build" / "retrain-data")

import mlflow  # noqa: E402
import pandas as pd  # noqa: E402
from register import register  # noqa: E402
from train_remote import SELECTED  # noqa: E402  the same settings as v1

from cloudlayer.factory import get_adapter  # noqa: E402
from src import config, data_source, evaluate, features, forecast, retrain  # noqa: E402

NAMES = ("targets.csv", "weather_daily.csv")
PRODUCTION = REPO_ROOT / "config" / "production.json"


def emit(event: str, **fields) -> None:
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "event": event, **fields}), flush=True)


def git_commit() -> str:
    """Cloud job: GIT_COMMIT is set at deploy time. Laptop: refuse uncommitted code."""
    if os.environ.get("GIT_COMMIT"):
        return os.environ["GIT_COMMIT"]
    def git(*a):
        return subprocess.run(["git", *a], capture_output=True, text=True, check=True,
                              cwd=REPO_ROOT).stdout.strip()
    if git("status", "--porcelain"):
        sys.exit("Commit first: the job must train from a commit that exists.")
    return git("rev-parse", "HEAD")


def load_production(cfg):
    """The model config/production.json names, from the registry (as the service loads it)."""
    import mlflow.sklearn
    prod = json.loads(PRODUCTION.read_text())
    ml = get_adapter(cfg)._ml_client()
    with tempfile.TemporaryDirectory() as tmp:
        ml.models.download(name=prod["model"], version=str(prod["version"]), download_path=tmp)
        mlmodel = next(Path(tmp).rglob("MLmodel"))
        model = mlflow.sklearn.load_model(str(mlmodel.parent))
        meta = mlflow.models.Model.load(str(mlmodel)).metadata or {}
    return model, meta, prod


def report(path: Path, r: dict) -> None:
    def row(name, m):
        return (f"| {name} | {m['mae']:.3f} | {_f(m['mae_dry'])} | {m['recall']:.3f} | "
                f"{m['false_alarm_rate']:.3f} | {m['bad_days']} |")
    def mark(v):
        return "n/a" if v is None else ("pass" if v else "**FAIL**")
    lines = [
        f"# Retraining gate, {r['date']}: **{r['decision'].upper()}**", "",
        f"- Data: targets `{r['data']['targets.csv'][:12]}…`, "
        f"weather `{r['data']['weather_daily.csv'][:12]}…` (from {r['data_from']})",
        f"- Window: {r['window'][0]} .. {r['window'][1]} ({r['rows']['window']:,} station-days);"
        f" challenger fitted on rows up to {r['train_end']} ({r['rows']['fit']:,})",
        f"- Challenger run `{r['run_id']}`, production v{r['production_version']}", "",
        "| on the window | MAE | MAE dry | recall | false alarms | days > 37.5 |",
        "|---|---|---|---|---|---|",
        row(f"challenger (seed {r['seed']})", r["challenger"]),
        row(f"production v{r['production_version']}", r["production"]),
        row("persistence", r["persistence"]), "",
        "Challenger MAE by seed: " + ", ".join(f"{s}: {v:.3f}" for s, v in r["seed_maes"].items())
        + f" (mean {r['seed_mae_mean']:.3f}; margin required {retrain.MARGIN:.3f})", "",
        "| check | result |", "|---|---|",
        *[f"| vs persistence: {k} | {mark(v)} |" for k, v in r["persistence_checks"].items()],
        *[f"| vs production: {k} | {mark(v)} |" for k, v in r["champion_checks"].items()], "",
        f"Registered: {r.get('registered_version') or 'nothing'}. "
        "Promotion to production needs scripts/promote.py and a reviewed pull request.", "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _f(x):
    return "n/a" if x is None else f"{x:.3f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=os.environ.get("IMAGE_DIGEST"),
                    help="image by digest (the cloud job gets it from IMAGE_DIGEST)")
    ap.add_argument("--compute", default="itcs3556688020-cpu")
    ap.add_argument("--window-days", type=int, default=retrain.WINDOW_DAYS)
    ap.add_argument("--no-register", action="store_true", help="judge only; register nothing")
    args = ap.parse_args()
    if not args.image or "@sha256:" not in args.image:
        sys.exit("give --image <registry>/<repo>@sha256:... (by digest, not by tag)")

    cfg = config.load()
    commit = git_commit()
    md5, data_from = data_source.serving_versions(NAMES, cfg.blob_uri)
    print(f"data from {data_from}: targets {md5['targets.csv'][:8]}, weather {md5['weather_daily.csv'][:8]}")

    # 1. challenger: an Azure ML job on the training cluster, same image and settings as v1
    adapter = get_adapter(cfg)
    command = ("mkdir -p $DATA_DIR && "
               "cp ${{inputs.targets}} $DATA_DIR/targets.csv && "
               "cp ${{inputs.weather}} $DATA_DIR/weather_daily.csv && "
               f"python -m src.retrain {SELECTED} --experiment pm25-retrain "
               f"--window-days {args.window_days} --metrics-out /tmp/retrain.json")
    job_id = adapter.submit_training(args.image, {
        "inputs": {"targets": data_source.dvc_key(md5["targets.csv"]),
                   "weather": data_source.dvc_key(md5["weather_daily.csv"])},
        "env": {"GIT_COMMIT": commit, "GIT_DIRTY": "False", "IMAGE_DIGEST": args.image,
                "DATA_DIR": "/tmp/pm25data",
                "DATA_VERSION_TARGETS": md5["targets.csv"],
                "DATA_VERSION_WEATHER": md5["weather_daily.csv"]},
        "command": command,
        "experiment": "pm25-retrain",
        "display_name": f"retrain-{datetime.now(timezone.utc):%Y%m%d}",
        "environment_name": "pm25-train",
        "compute": args.compute,
    })
    print(f"submitted {job_id}   commit {commit[:7]}")
    adapter.wait_training(job_id)

    mlflow.set_tracking_uri(cfg.mlflow_tracking_uri)
    with tempfile.TemporaryDirectory() as tmp:
        job = json.loads(Path(mlflow.artifacts.download_artifacts(
            run_id=job_id, artifact_path="retrain.json", dst_path=tmp)).read_text())

    # 2. production on the SAME rows: same data version, same features code, same window rule
    for n, path in (("targets.csv", cfg.targets_path), ("weather_daily.csv", cfg.weather_daily_path)):
        data_source.ensure(path, cfg.blob_uri, want=md5[n])
    df = features.build(pd.read_csv(cfg.targets_path), pd.read_csv(cfg.weather_daily_path),
                        pd.read_csv(cfg.stations_path))
    df = df[df["y"].notna()].copy()
    _, win, start, end = retrain.window(df, args.window_days)
    if [f"{start:%Y-%m-%d}", f"{end:%Y-%m-%d}"] != job["window"] or len(win) != job["rows"]["window"]:
        sys.exit(f"window mismatch: job {job['window']} ({job['rows']['window']} rows) vs here "
                 f"{start:%Y-%m-%d}..{end:%Y-%m-%d} ({len(win)} rows): not comparing different rows")
    model, meta, prod = load_production(cfg)
    pred = forecast.predict(model, win, meta["target_mode"], float(meta.get("shrink", 1.0)))
    production = retrain.clean(evaluate.metrics(win["y"].values, pred, win["lag1"].values,
                                                win["date"].dt.month.values))

    # 3. the gate
    seed_maes = list(job["seed_maes"].values())
    c_checks = retrain.champion_checks(seed_maes, job["challenger"], production)
    passed = retrain.decide(job["persistence_checks"], c_checks)
    client = mlflow.MlflowClient()
    client.set_tag(job_id, "champion_version", str(prod["version"]))
    client.set_tag(job_id, "champion_checks", json.dumps(c_checks))
    client.set_tag(job_id, "gate_passed", str(passed))

    r = {**job, "run_id": job_id, "date": f"{datetime.now(timezone.utc):%Y-%m-%d}",
         "data": md5, "data_from": data_from, "production_version": prod["version"],
         "production": production, "champion_checks": c_checks,
         "seed_mae_mean": sum(seed_maes) / len(seed_maes),
         "decision": "staging" if passed else "rejected", "registered_version": None}
    if passed and not args.no_register:
        r["registered_version"] = register(job_id)
    out = REPO_ROOT / "reports" / f"retrain-{r['date']}.md"
    report(out, r)
    client.log_artifact(job_id, str(out))
    emit("retrain", decision=r["decision"], run_id=job_id, window=job["window"],
         challenger_mae_mean=round(r["seed_mae_mean"], 3), production_mae=production["mae"],
         persistence_mae=job["persistence"]["mae"], production_version=prod["version"],
         registered_version=r["registered_version"], data_version=md5["targets.csv"])
    print(out.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())