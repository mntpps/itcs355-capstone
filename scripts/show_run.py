"""Reprint a run's VAL/TEST table from MLflow, without retraining.

    python scripts\\show_run.py <run_id>
"""
import sys

import mlflow

sys.path.insert(0, ".")
from src import config

mlflow.set_tracking_uri(config.load(strict=False).mlflow_tracking_uri)
run = mlflow.get_run(sys.argv[1])
m, p, t = run.data.metrics, run.data.params, run.data.tags
print(f"run {run.info.run_id}   {t.get('mlflow.runName', '')}   "
      f"target_mode={p.get('target_mode')}   features={p.get('features')}   loss={p.get('loss', 'squared_error')}")
print(f"\n{'':<18}{'VAL persist':>12}{'VAL model':>11}   {'TEST persist':>12}{'TEST model':>11}")
for k in ["mae", "mae_dry", "mae_rainy", "bias", "recall", "false_alarm_rate", "onset_recall"]:
    print(f"{k:<18}{m.get('val_persist_' + k, float('nan')):>12}{m.get('val_' + k, float('nan')):>11}"
          f"   {m.get('test_persist_' + k, float('nan')):>12}{m.get('test_' + k, float('nan')):>11}")
print(f"\nVALIDATION gate: {'passed' if t.get('val_gate_passed') == 'True' else 'failed'}"
      f"   TEST gate: {'PASSED' if t.get('gate_passed') == 'True' else 'FAILED'}")
if "invalid" in t:
    print("\n" + t["invalid"])