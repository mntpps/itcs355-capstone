"""Deploy the forecast service to Azure Container Apps BY IMAGE DIGEST, as a new revision.

    python scripts/deploy_service.py --image itcs3556688020.azurecr.io/itcs355@sha256:...

Settings come from cloud.env (it holds no secrets). The service logs in with the
user-assigned managed identity itcs3556688020-serve, so no credentials are passed.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from src import config  # noqa: E402

APP = "itcs3556688020-forecast"
ENVIRONMENT = "itcs3556688020-env"
IDENTITY = "itcs3556688020-serve"
SETTINGS = ["CLOUD_PROVIDER", "PROJECT_ID", "REGION", "BLOB_URI", "CONTAINER_REGISTRY",
            "MLFLOW_TRACKING_URI", "MODEL_REGISTRY_NAME", "IDENTITY_REF", "BUDGET_LIMIT_THB",
            "AZURE_ML_WORKSPACE", "AZURE_SUBSCRIPTION_ID"]


def az(*args, check=True):
    r = subprocess.run([shutil.which("az"), *args, "-o", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        if not check:
            return None
        sys.exit(f"az {' '.join(args[:3])} failed:\n{r.stderr}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="registry/repo@sha256:... (a digest, never a tag)")
    ap.add_argument("--min-replicas", type=int, default=0)
    args = ap.parse_args()
    if "@sha256:" not in args.image:
        sys.exit("refusing: deploy by digest (…@sha256:…), not by tag")

    config.load()
    rg = os.environ["PROJECT_ID"]
    version = json.loads((REPO_ROOT / "config" / "production.json").read_text())["version"]
    digest = args.image.split("@sha256:")[1]
    suffix = f"m{version}-{digest[:7]}-{time.strftime('%m%d%H%M')}"  # model, image, deploy time

    ident = az("identity", "show", "-g", rg, "-n", IDENTITY)
    env = az("containerapp", "env", "show", "-g", rg, "-n", ENVIRONMENT)
    settings = [{"name": k, "value": os.environ[k]} for k in SETTINGS if os.environ.get(k)]
    settings += [{"name": "AZURE_CLIENT_ID", "value": ident["clientId"]},
                 {"name": "IMAGE_DIGEST", "value": args.image}]

    spec = {
        "location": env["location"],
        "identity": {"type": "UserAssigned", "userAssignedIdentities": {ident["id"]: {}}},
        "properties": {
            "managedEnvironmentId": env["id"],
            "configuration": {
                "activeRevisionsMode": "Multiple",
                "ingress": {"external": True, "targetPort": 8000, "transport": "auto",
                            "traffic": [{"latestRevision": True, "weight": 100}]},
                "registries": [{"server": args.image.split("/")[0], "identity": ident["id"]}],
            },
            "template": {
                "revisionSuffix": suffix,
                "containers": [{
                    "name": "forecast",
                    "image": args.image,
                    "resources": {"cpu": 0.5, "memory": "1Gi"},
                    "env": settings,
                    "probes": [
                        {"type": "Startup", "httpGet": {"path": "/health", "port": 8000},
                         "periodSeconds": 2, "failureThreshold": 30},
                        {"type": "Liveness", "httpGet": {"path": "/health", "port": 8000},
                         "periodSeconds": 10, "failureThreshold": 3},
                        {"type": "Readiness", "httpGet": {"path": "/ready", "port": 8000},
                         "periodSeconds": 5, "failureThreshold": 3},
                    ],
                }],
                "scale": {"minReplicas": args.min_replicas, "maxReplicas": 3,
                          "rules": [{"name": "http", "http": {"metadata": {"concurrentRequests": "50"}}}]},
            },
        },
    }

    url = (f"https://management.azure.com/subscriptions/{os.environ['AZURE_SUBSCRIPTION_ID']}"
           f"/resourceGroups/{rg}/providers/Microsoft.App/containerApps/{APP}?api-version=2024-03-01")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(spec, f)
    print(f"deploying {APP}, revision {APP}--{suffix}")
    # PUT the whole definition: create-or-update, declarative. (The preview containerapp CLI
    # extension sends a malformed request for --yaml, so we call the API directly.)
    az("rest", "--method", "put", "--url", url, "--body", f"@{f.name}")
    os.unlink(f.name)

    state = "unknown"
    for _ in range(60):  # wait up to ~5 minutes for provisioning
        app = az("rest", "--method", "get", "--url", url)
        state = app["properties"]["provisioningState"]
        if state in ("Succeeded", "Failed", "Canceled"):
            break
        time.sleep(5)
    if state != "Succeeded":
        sys.exit(f"deployment ended in state {state}")

    fqdn = app["properties"]["configuration"]["ingress"]["fqdn"]
    print(f"deployed {args.image}\n  model v{version}, revision {APP}--{suffix}\n  https://{fqdn}/ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())