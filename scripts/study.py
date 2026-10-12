"""Lab 2 study: 16 trials, 4 hyperparameters varied, every trial tracked in MLflow.

Fixed: target_mode=delta, features=no_weather (Finding 8).
Selection rule, decided BEFORE running: among trials whose VALIDATION gate passes, the
lowest validation MAE wins. If none passes, nothing is promoted and the service keeps
serving persistence. Test numbers are never used to choose.
"""
import itertools
import subprocess
import sys
from pathlib import Path

GRID = {
    "--loss": ["squared_error", "absolute_error"],
    "--learning-rate": ["0.03", "0.1"],
    "--min-samples-leaf": ["40", "200"],
    "--max-leaf-nodes": ["15", "31"],
}
FIXED = ["--target-mode", "delta", "--features", "no_weather", "--experiment", "pm25-study"]

for i, combo in enumerate(itertools.product(*GRID.values()), 1):
    name = f"study-{i:02d}"
    out = Path(f"reports/study/{name}.json")
    if out.exists():
        print(f"skip {name}: already done")
        continue
    args = [x for pair in zip(GRID, combo) for x in pair]
    print(f"\n=== {name}: {' '.join(args)}", flush=True)
    subprocess.run([sys.executable, "-m", "src.train", *FIXED, *args, "--run-name", name,
                    "--metrics-out", str(out)], check=True)

BEST_VAL = ["--loss", "absolute_error", "--learning-rate", "0.03",
            "--min-samples-leaf", "200", "--max-leaf-nodes", "15"]       # study-11, chosen on VAL
for i, k in enumerate(["0.25", "0.5", "0.75"], 17):
    name = f"study-{i:02d}"
    out = Path(f"reports/study/{name}.json")
    if out.exists():
        print(f"skip {name}: already done")
        continue
    print(f"\n=== {name}: study-11 settings, --shrink {k}", flush=True)
    subprocess.run([sys.executable, "-m", "src.train", *FIXED, *BEST_VAL, "--shrink", k,
                    "--run-name", name, "--metrics-out", str(out)], check=True)