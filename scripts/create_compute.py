"""Create the training cluster if it doesn't exist. Scales to zero: no cost while idle.

Standard_DS2_V2 (2 vCPU): clusters don't support B-series sizes, and Azure for Students caps
regional vCPUs at 3, which cannot be raised.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from azure.ai.ml.entities import AmlCompute

from cloudlayer.factory import get_adapter
from src import config

NAME = "itcs3556688020-cpu"

cfg = config.load()
ml = get_adapter(cfg)._ml_client()
existing = [c.name for c in ml.compute.list()]
if NAME in existing:
    print(f"{NAME} already exists")
else:
    cluster = AmlCompute(name=NAME, size="Standard_DS2_V2", tier="Dedicated",
                         min_instances=0, max_instances=1,
                         idle_time_before_scale_down=120,      # seconds
                         tags=cfg.tags(5))
    print(f"creating {NAME} (Standard_DS2_V2, 0..1 nodes) ...")
    ml.compute.begin_create_or_update(cluster).result()
for c in ml.compute.list():
    print(c.name, c.type, c.size, "min", c.min_instances, "max", c.max_instances)