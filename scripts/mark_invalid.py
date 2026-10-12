"""Tag study runs whose logged parameters did not match the model actually trained."""
import json
import sys
from pathlib import Path

import mlflow

sys.path.insert(0, ".")
from src import config

REASON = "INVALID: --loss was logged but not passed to the model (fixed; see tests/test_train.py)"

mlflow.set_tracking_uri(config.load(strict=False).mlflow_tracking_uri)
client = mlflow.MlflowClient()
for n in range(9, 17):
    f = Path(f"reports/study/study-{n:02d}.json")
    run_id = json.loads(f.read_text())["run_id"]
    client.set_tag(run_id, "invalid", REASON)
    f.unlink()
    print(f"tagged {run_id[:8]} invalid, removed {f.name}")