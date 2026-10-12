"""Train one configuration, track it in MLflow, evaluate it honestly.

    python -m src.train --target-mode level --run-name level-1
    python -m src.train --target-mode delta --run-name delta-1

Two fits per run:
  1. fit on TRAIN, score on VALIDATION       -> val_* metrics: compare runs on these only
  2. refit on TRAIN+VALIDATION, score on TEST -> test_* metrics + the gate; the model saved
Every run logs all parameters incl. the seed, val and test metrics separately, the DVC
data version and the git commit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import os
from pathlib import Path

import mlflow
import mlflow.sklearn
import pandas as pd
import yaml
from sklearn.ensemble import HistGradientBoostingRegressor

from src import config, evaluate, features, forecast, seeds, split

SEASON = ["doy_sin", "doy_cos"]
FEATURE_SETS = {
    "all": features.FEATURES,
    "no_season": [f for f in features.FEATURES if f not in SEASON],
    "no_weather": [f for f in features.FEATURES if f not in features.WEATHER],
    "pm_only": ["lag1", "lag2", "lag3", "mean7", "city_lag1", "city_n"],
}


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              check=True, cwd=config.REPO_ROOT).stdout.strip()
    except Exception:
        return "unknown"


def data_version(path: Path) -> str:
    """The md5 DVC recorded for this file. If the file changed since `dvc add`, say so."""
    pointer = path.with_name(path.name + ".dvc")
    if not pointer.exists():
        return "not-dvc-tracked"
    recorded = yaml.safe_load(pointer.read_text())["outs"][0]["md5"]
    actual = hashlib.md5(path.read_bytes()).hexdigest()
    if actual != recorded:
        print(f"WARNING {path.name} changed since `dvc add`: this run is not reproducible "
              "until you run `dvc add` + `dvc push` and commit the .dvc file")
        return f"{actual}-not-committed"
    return recorded


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--target-mode", choices=forecast.TARGET_MODES, default="level")
    p.add_argument("--features", choices=list(FEATURE_SETS), default="all")
    p.add_argument("--learning-rate", type=float, default=0.05)
    p.add_argument("--max-iter", type=int, default=400)
    p.add_argument("--max-leaf-nodes", type=int, default=31)
    p.add_argument("--min-samples-leaf", type=int, default=40)
    p.add_argument("--l2", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=seeds.DEFAULT_SEED)
    p.add_argument("--experiment", default="pm25-capstone")
    p.add_argument("--run-name", default=None)
    p.add_argument("--metrics-out", type=Path, default=Path("reports/metrics.json"))
    p.add_argument("--enforce-gate", action="store_true",
                   help="exit 1 if the gate fails (the pipeline uses this; the study does not)")
    p.add_argument("--loss", choices=["squared_error", "absolute_error"], default="squared_error")
    p.add_argument("--shrink", type=float, default=1.0,
                    help="delta mode: forecast = yesterday + shrink * predicted change")
    return p.parse_args()


def fit(rows: pd.DataFrame, cols: list[str], args, seed: int):
    target = rows["y"] - rows["lag1"] if args.target_mode == "delta" else rows["y"]
    model = HistGradientBoostingRegressor(
        loss=args.loss,
        learning_rate=args.learning_rate, max_iter=args.max_iter,
        max_leaf_nodes=args.max_leaf_nodes, min_samples_leaf=args.min_samples_leaf,
        l2_regularization=args.l2, random_state=seed)
    return model.fit(rows[cols], target)


def score(model, rows: pd.DataFrame, target_mode: str, shrink: float) -> tuple[dict, dict]:
    month = rows["date"].dt.month.values
    pred = forecast.predict(model, rows, target_mode, shrink)
    m = evaluate.metrics(rows["y"].values, pred, rows["lag1"].values, month)
    base = evaluate.metrics(rows["y"].values, rows["lag1"].values, rows["lag1"].values, month)
    return m, base


def save_model(model, path: Path, target_mode: str, shrink: float) -> None:
    meta = {"target_mode": target_mode, "shrink": shrink}
    try:
        from skops.io import dumps, get_untrusted_types
        trusted = get_untrusted_types(data=dumps(model))   # our own model's types
        mlflow.sklearn.save_model(model, str(path), metadata=meta, skops_trusted_types=trusted)
    except ImportError:
        mlflow.sklearn.save_model(model, str(path), metadata=meta)


def main() -> int:
    args = parse_args()
    cfg = config.load(strict=False)
    seed = seeds.set_all(args.seed)

    t = pd.read_csv(cfg.targets_path)
    w = pd.read_csv(cfg.weather_daily_path)
    s = pd.read_csv(cfg.stations_path)
    df = features.build(t, w, s)
    df = df[df["y"].notna()].copy()
    df["split"] = split.assign(df["date"]).values
    train, val, test = (df[df["split"] == k] for k in ("train", "val", "test"))
    cols = FEATURE_SETS[args.features]
    m_val = fit(train, cols, args, seed)
    val_m, val_base = score(m_val, val, args.target_mode, args.shrink)
    m_final = fit(pd.concat([train, val]), cols, args, seed)
    test_m, test_base = score(m_final, test, args.target_mode, args.shrink)
    val_checks = evaluate.gate(val_m, val_base)
    checks = evaluate.gate(test_m, test_base)
    passed = all(checks.values())

    mlflow.set_tracking_uri(cfg.mlflow_tracking_uri)
    mlflow.set_experiment(args.experiment)
    with mlflow.start_run(run_name=args.run_name) as run:
        mlflow.log_params({**{k: v for k, v in vars(args).items()
                              if k not in ("metrics_out", "enforce_gate", "experiment", "run_name")},
                           "seed": seed, "n_features": len(cols)})
        mlflow.set_tags({
            "git_commit": os.environ.get("GIT_COMMIT") or git("rev-parse", "HEAD"),
            "git_uncommitted_changes": os.environ.get("GIT_DIRTY") or str(bool(git("status", "--porcelain"))),
            "training_job_id": os.environ.get("AZUREML_RUN_ID", "local"),
            "image_digest": os.environ.get("IMAGE_DIGEST", "local"),
            "data_version_targets": data_version(cfg.targets_path),
            "data_version_weather": data_version(cfg.weather_daily_path),
            "split": "val 2024-11-01..2025-04-30 | test 2025-10-01..2026-09-30 | gap 7d",
            "n_train": len(train), "n_val": len(val), "n_test": len(test),
            "val_gate_passed": str(all(val_checks.values())),
            "gate_passed": str(passed),
        })
        for prefix, d in [("val_", val_m), ("val_persist_", val_base),
                          ("test_", test_m), ("test_persist_", test_base)]:
            mlflow.log_metrics({prefix + k: v for k, v in d.items()})
        with tempfile.TemporaryDirectory() as tmp:
            save_model(m_final, Path(tmp) / "model", args.target_mode, args.shrink)
            mlflow.log_artifacts(str(Path(tmp) / "model"), artifact_path="model")
        run_id = run.info.run_id

    print(f"run {run_id}   target_mode={args.target_mode}   features={args.features}")
    print(f"rows: train {len(train):,}  val {len(val):,}  test {len(test):,}\n")
    print(f"{'':<18}{'VAL persist':>12}{'VAL model':>11}   {'TEST persist':>12}{'TEST model':>11}")
    for k in ["mae", "mae_dry", "mae_rainy", "bias", "recall", "false_alarm_rate", "onset_recall"]:
        mark = "" if k not in checks else ("  pass" if checks[k] else "  FAIL")
        print(f"{k:<18}{val_base[k]:>12}{val_m[k]:>11}   {test_base[k]:>12}{test_m[k]:>11}{mark}")
    print(f"\nVALIDATION gate: {'passed' if all(val_checks.values()) else 'failed'}"
          f"   TEST gate: {'PASSED' if passed else 'FAILED'}")

    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(json.dumps({
        "run_id": run_id, "gate_passed": passed, "gate_checks": checks,
        "params": {k: str(v) for k, v in vars(args).items()},
        "val": val_m, "val_persistence": val_base, "test": test_m, "test_persistence": test_base,
    }, indent=2))
    return 1 if (args.enforce_gate and not passed) else 0


if __name__ == "__main__":
    sys.exit(main())