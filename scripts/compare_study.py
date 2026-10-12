"""Rank the study by VALIDATION only, apply the selection rule, write the comparison."""
import json
from pathlib import Path

rows = []
for f in sorted(Path("reports/study").glob("*.json")):
    d = json.loads(f.read_text())
    v, vb, p = d["val"], d["val_persistence"], d["params"]
    gate = (v["mae"] < vb["mae"] and v["mae_dry"] < vb["mae_dry"]
            and v["recall"] >= vb["recall"] and v["false_alarm_rate"] <= vb["false_alarm_rate"])
    rows.append((f.stem, p["loss"], p["learning_rate"], p["min_samples_leaf"],
                 p["max_leaf_nodes"] + " / k=" + p.get("shrink", "1.0"), v["mae"], v["bias"],
                 v["recall"], v["false_alarm_rate"], v["onset_recall"], gate, d["run_id"]))

rows.sort(key=lambda r: (not r[10], r[5]))       # gate-passers first, then lowest val MAE
head = "| trial | loss | lr | min_leaf | leaves | val MAE | bias | recall | false alarms | onset recall | val gate | run |"
lines = [head, "|" + "---|" * 12]
lines += ["| " + " | ".join(str(x) for x in r[:10]) + f" | {'PASS' if r[10] else 'fail'} | {r[11][:8]} |"
          for r in rows]
base = json.loads(next(Path("reports/study").glob("*.json")).read_text())["val_persistence"]
lines += ["", f"Persistence on validation: MAE {base['mae']}, recall {base['recall']}, "
              f"false alarms {base['false_alarm_rate']}"]
winner = next((r for r in rows if r[10]), None)
lines += ["", f"**Selected:** {winner[0]} (run {winner[11]})" if winner
          else "**Selected:** none — no trial passed the validation gate; persistence stays in service."]
Path("reports/study-comparison.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))