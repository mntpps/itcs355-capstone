"""Weekly retraining, job side: fit the selected configuration on everything before a
recent window, score it on that window, log it with full lineage.

Runs as an Azure ML job started by scripts/retrain.py, which then scores the PRODUCTION
model on the same rows and applies the gate (the functions at the bottom of this file).

Window: the last WINDOW_DAYS dates that have a known next-day value. The challenger is
fitted only on rows ending GAP before the window (7 days, as in split.py, because lag and
7-day-mean inputs reach back 7 days). Production v1 was fitted on data up to 2025-09, so
neither model has seen the window: both are scored out of sample on the same rows.

Five seeds are fitted. The model saved (and possibly shipped) is the registered seed's;
the other four only measure how much of a difference is seed luck.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd

from src import config, data_source, evaluate, features, seeds, split, train

WINDOW_DAYS = 28
SEEDS = (seeds.DEFAULT_SEED, 1, 2, 3, 4)
SEED_SD = 0.028          # val MAE spread over these five seeds for study-17 (README)
MARGIN = 2 * SEED_SD     # a challenger must beat production by more than seed luck
MIN_BAD_DAYS = 20        # fewer station-days above 37.5 than this: too few to judge recall


# ---------- the window and the gate (pure; tested in tests/test_retrain.py) ----------

def window(df: pd.DataFrame, days: int = WINDOW_DAYS, gap: pd.Timedelta = split.GAP):
    """(rows to fit on, window rows, window start, window end)."""
    end = df["date"].max()
    start = end - pd.Timedelta(days=days - 1)
    return df[df["date"] < start - gap], df[df["date"] >= start], start, end


def persistence_checks(model: dict, base: dict) -> dict:
    """evaluate.gate on the window, with None for checks the window cannot judge:
    no dry-season days -> mae_dry is NaN; no days above the threshold -> recall is empty."""
    checks = evaluate.gate(model, base)
    if _nan(model["mae_dry"]) or _nan(base["mae_dry"]):
        checks["mae_dry"] = None
    if base["bad_days"] < MIN_BAD_DAYS:     # a handful of isolated spikes (often 0 >= 0) proves nothing
        checks["recall"] = None
    return checks


def champion_checks(seed_maes: list[float], challenger: dict, production: dict,
                    margin: float = MARGIN) -> dict:
    """The challenger must beat PRODUCTION, not only persistence."""
    return {
        "mean_mae_beats_production_by_margin": float(np.mean(seed_maes)) <= production["mae"] - margin,
        "every_seed_beats_production": max(seed_maes) < production["mae"],
        "recall_not_below_production": (None if production["bad_days"] < MIN_BAD_DAYS
                                        else challenger["recall"] >= production["recall"]),
    }


def decide(*check_sets: dict) -> bool:
    """Pass only if no check failed and at least one check could actually be judged."""
    values = [v for checks in check_sets for v in checks.values()]
    return all(v is not False for v in values) and any(v is True for v in values)


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def clean(d: dict) -> dict:
    """NaN -> None, so reports are valid JSON."""
    return {k: (None if _nan(v) else v) for k, v in d.items()}


# ---------- the job ----------

def _data_versions(cfg) -> dict:
    """The md5 the launcher said to use, checked against the files this job received."""
    out = {}
    for path, env in ((cfg.targets_path, "DATA_VERSION_TARGETS"),
                      (cfg.weather_daily_path, "DATA_VERSION_WEATHER")):
        want, got = os.environ.get(env), data_source.file_md5(path)
        if want and got != want:
            sys.exit(f"{path.name}: md5 {got[:8]} does not match the expected version {want[:8]}")
        out[env] = want or f"{got}-not-committed"      # a local run has no named version
    return out


def main(argv=None) -> int:
    p = train.build_parser()
    p.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    p.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    args = p.parse_args(argv)
    if args.run_name is None:
        args.run_name = "retrain"

    cfg = config.load(strict=False)
    versions = _data_versions(cfg)
    df = features.build(pd.read_csv(cfg.targets_path), pd.read_csv(cfg.weather_daily_path),
                        pd.read_csv(cfg.stations_path))
    df = df[df["y"].notna()].copy()
    fit_rows, win, start, end = window(df, args.window_days)
    cols = train.FEATURE_SETS[args.features]
    seed_list = list(dict.fromkeys([args.seed, *map(int, args.seeds.split(","))]))

    per_seed, shipped = {}, None
    for s in seed_list:
        seeds.set_all(s)
        model = train.fit(fit_rows, cols, args, s)
        m, base = train.score(model, win, args.target_mode, args.shrink)
        per_seed[s] = m
        if s == args.seed:
            shipped = model
        print(f"seed {s:>9}: window MAE {m['mae']:.3f}   (persistence {base['mae']:.3f})")
    challenger = per_seed[args.seed]
    seed_maes = [per_seed[s]["mae"] for s in seed_list]
    p_checks = persistence_checks(challenger, base)
    result = {
        "window": [f"{start:%Y-%m-%d}", f"{end:%Y-%m-%d}"],
        "train_end": f"{fit_rows['date'].max():%Y-%m-%d}",
        "rows": {"fit": len(fit_rows), "window": len(win)},
        "seed": args.seed, "seed_maes": dict(zip(map(str, seed_list), seed_maes)),
        "challenger": clean(challenger), "persistence": clean(base),
        "persistence_checks": p_checks,
    }

    mlflow.set_tracking_uri(cfg.mlflow_tracking_uri)
    mlflow.set_experiment(args.experiment)
    with mlflow.start_run(run_name=args.run_name) as run:
        mlflow.log_params({**{k: v for k, v in vars(args).items()
                              if k not in ("metrics_out", "enforce_gate", "experiment", "run_name")},
                           "seed": args.seed, "n_features": len(cols)})
        mlflow.set_tags({
            "kind": "retrain",
            "git_commit": os.environ.get("GIT_COMMIT") or train.git("rev-parse", "HEAD"),
            "git_uncommitted_changes": (os.environ.get("GIT_DIRTY")
                                        or str(bool(train.git("status", "--porcelain")))),
            "training_job_id": os.environ.get("AZUREML_RUN_ID", "local"),
            "image_digest": os.environ.get("IMAGE_DIGEST", "local"),
            "data_version_targets": versions["DATA_VERSION_TARGETS"],
            "data_version_weather": versions["DATA_VERSION_WEATHER"],
            "window": f"{start:%Y-%m-%d}..{end:%Y-%m-%d}",
            "train_end": result["train_end"],
            "n_fit": len(fit_rows), "n_window": len(win),
            "persistence_gate": json.dumps(p_checks),
            "gate_passed": "pending",          # the launcher sets it after comparing with production
            "metric_note": f"MAE ug/m3 on window {start:%Y-%m-%d}..{end:%Y-%m-%d}; "
                           f"persistence {base['mae']:.3f}",
        })
        mlflow.log_metrics({f"window_{k}": v for k, v in challenger.items()})
        mlflow.log_metrics({f"window_persist_{k}": v for k, v in base.items()})
        mlflow.log_metrics({f"window_mae_seed_{s}": v for s, v in zip(seed_list, seed_maes)})
        mlflow.log_metrics({"window_mae_mean": float(np.mean(seed_maes)),
                            "window_mae_sd": float(np.std(seed_maes, ddof=1)) if len(seed_maes) > 1 else 0.0})
        with tempfile.TemporaryDirectory() as tmp:
            train.save_model(shipped, Path(tmp) / "model", args.target_mode, args.shrink)
            mlflow.log_artifacts(str(Path(tmp) / "model"), artifact_path="model")
            (Path(tmp) / "retrain.json").write_text(json.dumps(result, indent=2))
            mlflow.log_artifact(str(Path(tmp) / "retrain.json"))
        result["run_id"] = run.info.run_id

    print(f"\nrun {result['run_id']}   window {result['window'][0]}..{result['window'][1]}"
          f"   fitted on rows up to {result['train_end']}")
    print(f"challenger MAE (seed {args.seed}) {challenger['mae']:.3f}   "
          f"mean of {len(seed_maes)} seeds {np.mean(seed_maes):.3f}   persistence {base['mae']:.3f}")
    print("persistence checks: " + ", ".join(
        f"{k}={'n/a' if v is None else ('pass' if v else 'FAIL')}" for k, v in p_checks.items()))
    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())