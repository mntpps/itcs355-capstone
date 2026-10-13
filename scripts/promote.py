"""Propose promoting a registered model version to production.

config/production.json (committed) is the single source of truth for which
version serves. This script only *proposes* a change: it checks the version is
promotable and rewrites the file. The change reaches production only when a
reviewed pull request is merged.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from azure.ai.ml import MLClient
from azure.identity import DefaultAzureCredential

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src import config  # noqa: E402  (must come after the path line above)
PROD = REPO_ROOT / "config" / "production.json"
REQUIRED = ["git_commit", "data_version", "image_digest", "mlflow_run_id",
            "training_job_id", "seed", "metric_val", "metric_test",
            "target_mode", "shrink"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--reason", required=True)
    args = p.parse_args()

    cfg = config.load()
    name = cfg.model_registry_name
    ml = MLClient(DefaultAzureCredential(), os.environ["AZURE_SUBSCRIPTION_ID"],
                  cfg.project_id, os.environ["AZURE_ML_WORKSPACE"])
    model = ml.models.get(name=name, version=str(args.version))
    tags = model.tags or {}

    missing = [k for k in REQUIRED if not tags.get(k)]
    if missing:
        sys.exit(f"REFUSED: {name} v{args.version} is missing lineage tags {missing}")
    if tags.get("stage") != "staging":
        sys.exit(f"REFUSED: {name} v{args.version} has stage={tags.get('stage')!r}, "
                 "only gate-passed 'staging' models can be promoted")

    current = json.loads(PROD.read_text()) if PROD.exists() else None
    if current and current["version"] == args.version:
        sys.exit(f"{name} v{args.version} is already in production")

    record = {
        "model": name,
        "version": args.version,
        "previous_version": current["version"] if current else None,
        "mlflow_run_id": tags["mlflow_run_id"],
        "git_commit": tags["git_commit"],
        "metric_val": float(tags["metric_val"]),
        "metric_test": float(tags["metric_test"]),
        "reason": args.reason,
    }
    PROD.write_text(json.dumps(record, indent=2) + "\n", newline="\n")
    print(json.dumps(record, indent=2))
    print(f"\nWrote {PROD.relative_to(REPO_ROOT)}. Open a pull request to promote.")


if __name__ == "__main__":
    main()