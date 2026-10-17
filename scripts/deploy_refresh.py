"""Create/update the daily data-refresh job (Azure Container Apps Job), BY IMAGE DIGEST.

    python scripts/deploy_refresh.py --image itcs3556688020.azurecr.io/itcs355@sha256:...
    python scripts/deploy_refresh.py --run-now --dry-run    # reads blob, uploads nothing
    python scripts/deploy_refresh.py --run-now              # one real refresh now
    python scripts/deploy_refresh.py --run-now --days 14    # re-fetch a longer window once

The job runs `python -m src.refresh` every day at 06:30 Bangkok. It runs as identity
itcs3556688020-refresh, the only identity that can WRITE data: AcrPull, Key Vault
Secrets User on the one OpenAQ secret, Storage Blob Data Contributor on the data
container. The OpenAQ key is a Key Vault reference; its value never passes here.
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

JOB = "itcs3556688020-refresh"
ENVIRONMENT = "itcs3556688020-env"
IDENTITY = "itcs3556688020-refresh"
VAULT = "itcs3556688020-kv"
CRON = "30 23 * * *"            # UTC 23:30 = 06:30 Bangkok: yesterday is complete
API = "api-version=2024-03-01"
RESOURCES = {"cpu": 0.5, "memory": "1Gi"}


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
    ap.add_argument("--dry-run", action="store_true", help="with --run-now: read blob, upload nothing")
    ap.add_argument("--days", type=int, help="with --run-now: re-fetch this many days instead of 10")
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
                    "replicaTimeout": 1800,
                    "replicaRetryLimit": 0,     # a failed run publishes nothing; tomorrow's run retries
                    "secrets": [{"name": "openaq-api-key",
                                 "keyVaultUrl": f"https://{VAULT}.vault.azure.net/secrets/openaq-api-key",
                                 "identity": ident["id"]}],
                    "registries": [{"server": args.image.split("/")[0], "identity": ident["id"]}],
                },
                "template": {"containers": [{
                    "name": "refresh",
                    "image": args.image,
                    "command": ["python", "-m", "src.refresh"],
                    "env": [{"name": "OPENAQ_API_KEY", "secretRef": "openaq-api-key"},
                            # DefaultAzureCredential: use THIS identity for blob storage
                            {"name": "AZURE_CLIENT_ID", "value": ident["clientId"]},
                            {"name": "IMAGE_DIGEST", "value": args.image.split("@")[1]},
                            *settings],
                    "resources": RESOURCES,
                }]},
            },
        }
        rest("put", job_url, spec)
        print(f"job {JOB}: {args.image}\n  schedule '{CRON}' (UTC) = 06:30 Bangkok, "
              f"identity {IDENTITY}, {len(settings)} settings")

    if args.run_now:
        body = None
        if args.dry_run or args.days:
            container = rest("get", job_url)["properties"]["template"]["containers"][0]
            command = ["python", "-m", "src.refresh"]
            command += ["--dry-run"] if args.dry_run else []
            command += ["--days", str(args.days)] if args.days else []
            body = {"containers": [{"name": "refresh", "image": container["image"],
                                    "command": command, "env": container["env"],
                                    "resources": RESOURCES}]}
        execution = rest("post", f"{base}/jobs/{JOB}/start?{API}", body)
        extra = " ".join(x for x in ("--dry-run" if args.dry_run else "",
                                     f"--days {args.days}" if args.days else "") if x)
        print(f"started execution {execution.get('name', execution)}{' (' + extra + ')' if extra else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())