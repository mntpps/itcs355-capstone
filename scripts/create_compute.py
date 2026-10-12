"""Create the training cluster if it doesn't exist. Scales to zero: no cost while idle.

Standard_DS2_V2 (2 vCPU): clusters don't support B-series sizes, and Azure for Students caps
regional vCPUs at 3, which cannot be raised.
The cluster gets its own managed identity; it is granted only AcrPull (pull the training
image) and Storage Blob Data Reader (read the DVC data). See README, "Identities".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from azure.ai.ml.entities import AmlCompute, IdentityConfiguration

from cloudlayer.factory import get_adapter
from src import config

NAME = "itcs3556688020-cpu"
SIZE = "Standard_DS2_V2"

cfg = config.load()
ml = get_adapter(cfg)._ml_client()
existing = [c.name for c in ml.compute.list()]
if NAME in existing:
    current = ml.compute.get(NAME)
    if current.size.lower() != SIZE.lower() or current.provisioning_state == "Failed":
        sys.exit(f"{NAME} exists as {current.size} ({current.provisioning_state}); "
                 f"delete it and rerun to get {SIZE}")
    print(f"{NAME} already exists ({current.size})")
else:
    cluster = AmlCompute(name=NAME, size=SIZE, tier="Dedicated",
                         min_instances=0, max_instances=1,
                         idle_time_before_scale_down=120,        # seconds
                         identity=IdentityConfiguration(type="system_assigned"),
                         tags=cfg.tags(5))
    print(f"creating {NAME} ({SIZE}, 0..1 nodes, system-assigned identity) ...")
    ml.compute.begin_create_or_update(cluster).result()

c = ml.compute.get(NAME)
print(c.name, c.size, "min", c.min_instances, "max", c.max_instances,
      "identity", c.identity.principal_id if c.identity else None)