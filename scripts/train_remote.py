"""Retrain the selected configuration (study-17) as an Azure ML job.

The job's data inputs ARE the DVC objects on Azure Blob, addressed by the md5 in
data/*.dvc. Inside the job, train.py re-checks each file's md5 against its .dvc pointer,
so the run provably used the data version committed in git.

    python scripts/train_remote.py --image <registry>/<repo>@sha256:...
"""
import argparse
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloudlayer.factory import get_adapter
from src import config

SELECTED = ("--target-mode delta --features no_weather --loss absolute_error "
            "--learning-rate 0.03 --min-samples-leaf 200 --max-leaf-nodes 15 --shrink 0.25")


def dvc_key(pointer: str) -> str:
    """Where DVC 3 stores a file on the remote: dvc/files/md5/<first 2>/<rest>."""
    md5 = yaml.safe_load(Path(pointer).read_text())["outs"][0]["md5"]
    return f"dvc/files/md5/{md5[:2]}/{md5[2:]}"


def git(*a: str) -> str:
    return subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="image by digest, from push_image")
    ap.add_argument("--compute", default="itcs3556688020-cpu")
    args = ap.parse_args()

    if git("status", "--porcelain"):
        sys.exit("Commit first: the job must train from a commit that exists.")
    commit = git("rev-parse", "HEAD")

    command = ("mkdir -p data && cp ${{inputs.targets}} data/targets.csv && "
               "cp ${{inputs.weather}} data/weather_daily.csv && "
               f"python -m src.train {SELECTED} --experiment pm25-capstone --enforce-gate")

    adapter = get_adapter(config.load())
    job_id = adapter.submit_training(args.image, {
        "inputs": {"targets": dvc_key("data/targets.csv.dvc"),
                   "weather": dvc_key("data/weather_daily.csv.dvc")},
        "env": {"GIT_COMMIT": commit, "GIT_DIRTY": "False", "IMAGE_DIGEST": args.image},
        "command": command,
        "experiment": "pm25-capstone",
        "display_name": "pm25-study17",
        "environment_name": "pm25-train",
        "compute": args.compute,
    })
    print(f"submitted {job_id}   commit {commit[:7]}")
    print(adapter.wait_training(job_id))


if __name__ == "__main__":
    main()