"""Load the registered model BY VERSION from the registry and forecast a few stations
(Lab 2, Task 5). Not from a local file: the point is to prove the registry copy works.

    python scripts/reload_check.py --version 1
"""
import argparse
import sys
import tempfile
from pathlib import Path

import mlflow
import mlflow.sklearn
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloudlayer.factory import get_adapter
from src import config, features, forecast


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--rows", type=int, default=5)
    args = ap.parse_args()

    cfg = config.load()
    ml = get_adapter(cfg)._ml_client()
    entry = ml.models.get(cfg.model_registry_name, args.version)
    print(f"registry: {entry.name} v{entry.version}  "
          f"commit {entry.tags.get('git_commit', '?')[:7]}  data {entry.tags.get('data_version', '?')[:8]}")

    with tempfile.TemporaryDirectory() as tmp:
        ml.models.download(name=cfg.model_registry_name, version=args.version, download_path=tmp)
        mlmodel = next(Path(tmp).rglob("MLmodel"))
        model = mlflow.sklearn.load_model(str(mlmodel.parent))
        meta = mlflow.models.Model.load(str(mlmodel)).metadata or {}

    df = features.build(pd.read_csv(cfg.targets_path), pd.read_csv(cfg.weather_daily_path),
                        pd.read_csv(cfg.stations_path))
    day = df["date"].max()
    rows = df[df["date"] == day].head(args.rows)
    pred = forecast.predict(model, rows, meta["target_mode"], float(meta.get("shrink", 1.0)))
    print(f"forecast for {day:%Y-%m-%d}  (target_mode={meta['target_mode']}, shrink={meta.get('shrink')})")
    for r, p in zip(rows.itertuples(), pred):
        print(f"  station {r.location_id:>8}   yesterday {r.lag1:6.1f}   forecast {p:6.1f} ug/m3")
    print("\nPASS  model reloaded from the registry by version and produced forecasts")
    return 0


if __name__ == "__main__":
    sys.exit(main())