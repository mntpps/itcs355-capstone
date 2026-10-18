"""Register a trained model with full lineage (Lab 2, Task 4).

    python scripts/register.py --run bright_seal_wsd8hyrstg

Refuses unless the run passed the gate, came from committed code, and every lineage
link is present. A registry entry that cannot say which code, data and image produced
it is decoration.
"""
import argparse
import sys
from pathlib import Path

import mlflow

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloudlayer.factory import get_adapter
from src import config

BAD = {None, "", "local", "unknown", "not-dvc-tracked"}


def register(run_id: str) -> str:
    """Register one run's model, or exit with the reason it is refused. Returns the version."""
    cfg = config.load()
    mlflow.set_tracking_uri(cfg.mlflow_tracking_uri)
    run = mlflow.get_run(run_id)
    t, p, m = run.data.tags, run.data.params, run.data.metrics

    if t.get("gate_passed") != "True":
        sys.exit("REFUSED: this run did not pass the evaluation gate")
    if t.get("git_uncommitted_changes") != "False":
        sys.exit("REFUSED: trained from uncommitted code")
    links = ["git_commit", "data_version_targets", "data_version_weather",
             "training_job_id", "image_digest"]
    broken = [k for k in links if t.get(k) in BAD or str(t.get(k)).endswith("-not-committed")]
    if broken:
        sys.exit(f"REFUSED: lineage incomplete: {broken}")

    retrain = t.get("kind") == "retrain"    # scored on a recent window, not the fixed val/test split
    lineage = {
        "git_commit": t["git_commit"],
        "data_version": t["data_version_targets"],
        "data_version_weather": t["data_version_weather"],
        "mlflow_run_id": run.info.run_id,
        "training_job_id": t["training_job_id"],
        "image_digest": t["image_digest"],
        "seed": p["seed"],
        "metric_val": m["window_mae_mean" if retrain else "val_mae"],
        "metric_test": m["window_mae" if retrain else "test_mae"],
        "metric": t.get("metric_note", "MAE ug/m3; persistence val 6.14, test 4.12"),
        "target_mode": p["target_mode"],
        "shrink": p["shrink"],
        "stage": "staging",
    }
    if retrain:
        lineage.update({"kind": "retrain", "window": t["window"], "train_end": t["train_end"],
                        "champion_version": t.get("champion_version", "")})
    model_uri = f"azureml://jobs/{run_id}/outputs/artifacts/paths/model/"
    version = get_adapter(cfg).register_model(model_uri, cfg.model_registry_name, tags=lineage)
    print(f"registered {cfg.model_registry_name} version {version}")
    for k, v in lineage.items():
        print(f"  {k:22} {v}")
    return version


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="training job / MLflow run id")
    register(ap.parse_args().run)



if __name__ == "__main__":
    main()