"""Create/update the hourly monitoring job (Azure Container Apps Job), BY IMAGE DIGEST.

    python scripts/deploy_monitor.py --image itcs3556688020.azurecr.io/itcs355@sha256:...
    python scripts/deploy_monitor.py --run-now
    python scripts/deploy_monitor.py --run-now --replay tests/fixtures/bang_sue_frozen_2023-09.csv

The OpenAQ key is never passed here: the job reads Key Vault secret `openaq-api-key`
through the monitor identity, which can read only that secret and pull the image.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from src import config  # noqa: E402

JOB = "itcs3556688020-monitor"
APP = "itcs3556688020-forecast"
ENVIRONMENT = "itcs3556688020-env"
IDENTITY = "itcs3556688020-monitor"
VAULT = "itcs3556688020-kv"
CRON = "5 * * * *"              # UTC: every hour at minute 5
API = "api-version=2024-03-01"


def az(*args, check=True):
    r = subprocess.run([shutil.which("az"), *args, "-o", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        if not check:
            return None
        sys.exit(f"az {' '.join(args[:3])} failed:\n{r.stderr}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def rest(method, url, body=None):
    args = ["rest", "--method", method, "--url", url]
    if body is None:
        return az(*args)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f)
    try:
        return az(*args, "--body", f"@{f.name}")
    finally:
        os.unlink(f.name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="registry/repo@sha256:... (create/update the job)")
    ap.add_argument("--run-now", action="store_true", help="start one execution now")
    ap.add_argument("--replay", help="with --run-now: replay this recorded incident instead of live")
    args = ap.parse_args()
    if not args.image and not args.run_now:
        sys.exit("give --image (deploy) and/or --run-now")
    if args.image and "@sha256:" not in args.image:
        sys.exit("refusing: deploy by digest (…@sha256:…), not by tag")

    config.load()
    rg, sub = os.environ["PROJECT_ID"], os.environ["AZURE_SUBSCRIPTION_ID"]
    base = f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.App"
    job_url = f"{base}/jobs/{JOB}?{API}"

    if args.image:
        ident = az("identity", "show", "-g", rg, "-n", IDENTITY)
        env = az("containerapp", "env", "show", "-g", rg, "-n", ENVIRONMENT)
        app = rest("get", f"{base}/containerApps/{APP}?{API}")
        service_url = "https://" + app["properties"]["configuration"]["ingress"]["fqdn"]
        spec = {
            "location": env["location"],
            "identity": {"type": "UserAssigned", "userAssignedIdentities": {ident["id"]: {}}},
            "properties": {
                "environmentId": env["id"],
                "configuration": {
                    "triggerType": "Schedule",
                    "scheduleTriggerConfig": {"cronExpression": CRON, "parallelism": 1,
                                              "replicaCompletionCount": 1},
                    "replicaTimeout": 900,
                    "replicaRetryLimit": 0,
                    # a REFERENCE to the secret; the value never passes through this script
                    "secrets": [{"name": "openaq-api-key",
                                 "keyVaultUrl": f"https://{VAULT}.vault.azure.net/secrets/openaq-api-key",
                                 "identity": ident["id"]}],
                    "registries": [{"server": args.image.split("/")[0], "identity": ident["id"]}],
                },
                "template": {"containers": [{
                    "name": "monitor",
                    "image": args.image,
                    "command": ["python", "scripts/monitor_run.py"],
                    "env": [{"name": "OPENAQ_API_KEY", "secretRef": "openaq-api-key"},
                            {"name": "SERVICE_URL", "value": service_url}],
                    "resources": {"cpu": 0.25, "memory": "0.5Gi"},
                }]},
            },
        }
        rest("put", job_url, spec)
        print(f"job {JOB}: {args.image}\n  schedule '{CRON}' (UTC), checks {service_url}")

    if args.run_now:
        body = None
        if args.replay:
            image = rest("get", job_url)["properties"]["template"]["containers"][0]["image"]
            body = {"containers": [{"name": "monitor", "image": image,
                                    "command": ["python", "scripts/monitor_run.py", "--replay", args.replay],
                                    "resources": {"cpu": 0.25, "memory": "0.5Gi"}}]}
        execution = rest("post", f"{base}/jobs/{JOB}/start?{API}", body)
        print(f"started execution {execution.get('name', execution)}"
              f"{' (replay ' + args.replay + ')' if args.replay else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())