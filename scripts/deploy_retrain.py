"""Create/update the weekly retraining job (Azure Container Apps Job), BY IMAGE DIGEST.

    python scripts/deploy_retrain.py --image itcs3556688020.azurecr.io/itcs355@sha256:...
    python scripts/deploy_retrain.py --run-now

Every Sunday at 07:30 Bangkok (an hour after that day's data refresh) the job runs
scripts/retrain.py: it submits the challenger to the Azure ML training cluster, scores
production on the same window, and registers the challenger as `staging` only if it
passes the gate. It never changes production: that stays promote.py -> PR -> canary.

Identity itcs3556688020-retrain: AcrPull, AzureML Data Scientist on the workspace (submit
jobs, read/register models, MLflow), Storage Blob Data Reader on the data container.
It cannot write data and holds no secret.

GIT_COMMIT is recorded at deploy time, so deploy from a clean, pushed commit: the job's
models are attributed to the code that is in the image.
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
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from deploy_service import SETTINGS  # noqa: E402  same settings as the service, one list
from src import config  # noqa: E402

JOB = "itcs3556688020-retrain"
ENVIRONMENT = "itcs3556688020-env"
IDENTITY = "itcs3556688020-retrain"
CRON = "30 0 * * 0"             # UTC Sunday 00:30 = Sunday 07:30 Bangkok
API = "api-version=2024-03-01"
RESOURCES = {"cpu": 1.0, "memory": "2Gi"}


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


def git(*a: str) -> str:
    return subprocess.run(["git", *a], capture_output=True, text=True, check=True,
                          cwd=REPO_ROOT).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="registry/repo@sha256:... built from the current commit")
    ap.add_argument("--run-now", action="store_true", help="start one execution now")
    args = ap.parse_args()
    if not args.image and not args.run_now:
        sys.exit("give --image (deploy) and/or --run-now")
    if args.image and "@sha256:" not in args.image:
        sys.exit("refusing: deploy by digest (...@sha256:...), not by tag")

    config.load()
    rg, sub = os.environ["PROJECT_ID"], os.environ["AZURE_SUBSCRIPTION_ID"]
    base = f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.App"
    job_url = f"{base}/jobs/{JOB}?{API}"

    if args.image:
        if git("status", "--porcelain"):
            sys.exit("Commit first: the job's models are attributed to GIT_COMMIT.")
        commit = git("rev-parse", "HEAD")
        ident = az("identity", "show", "-g", rg, "-n", IDENTITY)
        env = az("containerapp", "env", "show", "-g", rg, "-n", ENVIRONMENT)
        settings = [{"name": k, "value": os.environ[k]} for k in SETTINGS if os.environ.get(k)]
        spec = {
            "location": env["location"],
            "identity": {"type": "UserAssigned", "userAssignedIdentities": {ident["id"]: {}}},
            "properties": {
                "environmentId": env["id"],
                "configuration": {
                    "triggerType": "Schedule",
                    "scheduleTriggerConfig": {"cronExpression": CRON, "parallelism": 1,
                                              "replicaCompletionCount": 1},
                    "replicaTimeout": 3600,     # cluster start + 5 fits; ~10 min on Oct 18
                    "replicaRetryLimit": 0,     # a failed week registers nothing; next week retries
                    "registries": [{"server": args.image.split("/")[0], "identity": ident["id"]}],
                },
                "template": {"containers": [{
                    "name": "retrain",
                    "image": args.image,
                    "command": ["python", "scripts/retrain.py"],
                    "env": [{"name": "AZURE_CLIENT_ID", "value": ident["clientId"]},
                            {"name": "IMAGE_DIGEST", "value": args.image},
                            {"name": "GIT_COMMIT", "value": commit},
                            *settings],
                    "resources": RESOURCES,
                }]},
            },
        }
        rest("put", job_url, spec)
        print(f"job {JOB}: {args.image}\n  commit {commit[:7]}, schedule '{CRON}' (UTC) = "
              f"Sunday 07:30 Bangkok, identity {IDENTITY}, {len(settings)} settings")

    if args.run_now:
        execution = rest("post", f"{base}/jobs/{JOB}/start?{API}")
        print(f"started execution {execution.get('name', execution)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())