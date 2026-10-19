"""Create/update the operations dashboard (an Azure Monitor Workbook) from code.

    python scripts/create_dashboard.py            # create or update it, print its link
    python scripts/create_dashboard.py --print    # only print the workbook JSON

Every tile is a KQL query over the JSON events the service and jobs already write to
stdout (Container Apps -> Log Analytics): request, model_performance, refresh, finding,
retrain. The workbook's id is derived from the resource group, so running this again
UPDATES the same dashboard instead of creating a copy.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from src import config  # noqa: E402

ENVIRONMENT = "itcs3556688020-env"
TITLE = "PM2.5 forecast - operations"
API = "api-version=2022-04-01"
DAY_MS, WEEK_MS = 86_400_000, 7 * 86_400_000

LOGS = "ContainerAppConsoleLogs_CL"


def events(name: str) -> str:
    """Rows of one event type, parsed. Every emitter writes json.dumps(...) -> '"event": "<name>"'."""
    return f'{LOGS}\n| where Log_s has \'"event": "{name}"\'\n| extend e = parse_json(Log_s)\n'


TILES = [
    {"title": "Traffic: requests and server errors per hour (0 when idle)",
     "query": events("request") +
     "| make-series requests = count(), errors_5xx = countif(toint(e.status) >= 500) default = 0"
     " on TimeGenerated from ago(7d) to now() step 1h",
     "viz": "timechart", "range": WEEK_MS},
    {"title": "Service SLOs per day: availability >= 99%, p95 latency <= 500 ms",
     "query": events("request") +
     "| summarize requests = count(), errors_5xx = countif(toint(e.status) >= 500),"
     " p50_ms = round(percentile(todouble(e.latency_ms), 50), 0),"
     " p95_ms = round(percentile(todouble(e.latency_ms), 95), 0) by day = startofday(TimeGenerated)\n"
     "| extend availability_pct = round(100.0 * (requests - errors_5xx) / requests, 2)\n"
     "| extend slo_met = iff(availability_pct >= 99 and p95_ms <= 500, 'yes', 'NO')\n"
     "| project day = format_datetime(day, 'yyyy-MM-dd'), requests, errors_5xx, availability_pct,"
     " p50_ms, p95_ms, slo_met\n| order by day desc",
     "viz": "table", "range": WEEK_MS},
    {"title": "Model quality: MAE of the serving model vs persistence, last 14 days with actuals",
     "query": events("model_performance") +
     "| summarize model = avg(todouble(e.mae_model)), persistence = avg(todouble(e.mae_persistence))"
     " by bin(TimeGenerated, 1h)",
     "viz": "timechart", "range": WEEK_MS},
    {"title": "Data freshness: daily refreshes (serving manifest)",
     "query": events("refresh") +
     "| extend t = e.files['targets.csv']\n"
     "| project TimeGenerated, last_date = format_datetime(todatetime(tostring(t.last_date)), 'yyyy-MM-dd'),"
     " age_days = datetime_diff('day', now(), todatetime(tostring(t.last_date))),"
     " rows = toint(t.rows), stations_failed = array_length(e.stations_failed),"
     " base = tostring(e.base['from']), md5 = substring(tostring(t.md5), 0, 8)\n"
     "| order by TimeGenerated desc",
     "viz": "table", "range": WEEK_MS},
    {"title": "Monitor findings per day, by check",
     "query": events("finding") +
     "| summarize findings = count() by bin(TimeGenerated, 1d), check = strcat(tostring(e.check),"
     " iff(tostring(e.mode) == 'replay', ' (replay)', ''))",
     "viz": "barchart", "range": WEEK_MS},
    {"title": "Weekly retraining: gate decisions",
     "query": events("retrain") +
     "| project TimeGenerated, decision = tostring(e.decision),"
     " window = strcat(tostring(e.window[0]), ' .. ', tostring(e.window[1])),"
     " challenger_mae = todouble(e.challenger_mae_mean), production_mae = todouble(e.production_mae),"
     " persistence_mae = todouble(e.persistence_mae), registered = tostring(e.registered_version)\n"
     "| order by TimeGenerated desc",
     "viz": "table", "range": 30 * DAY_MS},
]


def workbook(workspace_id: str) -> dict:
    items = [{"type": 1, "name": "intro", "content": {"json": (
        f"## {TITLE}\n"
        "Every tile is a query over events the service and jobs log themselves. "
        "Source: `scripts/create_dashboard.py` in the repo.")}}]
    for i, t in enumerate(TILES, 1):
        items.append({
            "type": 3, "name": f"tile-{i}", "customWidth": "50",
            "content": {
                "version": "KqlItem/1.0", "query": t["query"], "size": 0, "title": t["title"],
                "timeContext": {"durationMs": t["range"]},
                "queryType": 0, "resourceType": "microsoft.operationalinsights/workspaces",
                "crossComponentResources": [workspace_id],
                "visualization": t["viz"],
                # the legend's big numbers would be SUMS of MAEs and latencies: meaningless, so hidden
                **({"chartSettings": {"showMetrics": False, "showLegend": True}}
                   if t["viz"] == "timechart" else {}),
                **({"chartSettings": {"group": "check", "createOtherGroup": 0, "showMetrics": False}}
                   if t["viz"] == "barchart" else {}),
            },
        })
    return {"version": "Notebook/1.0", "items": items, "fallbackResourceIds": [workspace_id],
            "$schema": "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json"}


def az(*args):
    r = subprocess.run([shutil.which("az"), *args, "-o", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"az {' '.join(args[:3])} failed:\n{r.stderr}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="print the workbook JSON, deploy nothing")
    args = ap.parse_args()
    if args.print:
        print(json.dumps(workbook("<workspace id>"), indent=2))
        return 0

    config.load()
    rg, sub = os.environ["PROJECT_ID"], os.environ["AZURE_SUBSCRIPTION_ID"]
    env = az("containerapp", "env", "show", "-g", rg, "-n", ENVIRONMENT)
    customer_id = env["properties"]["appLogsConfiguration"]["logAnalyticsConfiguration"]["customerId"]
    ws = next(w for w in az("monitor", "log-analytics", "workspace", "list", "-g", rg)
              if w["customerId"] == customer_id)
    name = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{sub}/{rg}/pm25-operations"))   # stable: re-run updates
    url = (f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
           f"/providers/Microsoft.Insights/workbooks/{name}?{API}")
    body = {"location": ws["location"], "kind": "shared",
            "tags": {"course": "itcs355", "student": rg},
            "properties": {"displayName": TITLE, "category": "workbook", "version": "Notebook/1.0",
                           "sourceId": ws["id"], "serializedData": json.dumps(workbook(ws["id"]))}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f)
    try:
        az("rest", "--method", "put", "--url", url, "--body", f"@{f.name}")
    finally:
        os.unlink(f.name)
    print(f"workbook '{TITLE}' ({len(TILES)} tiles) on workspace {ws['name']}")
    print(f"open: https://portal.azure.com/#@/resource/subscriptions/{sub}/resourceGroups/{rg}"
          f"/providers/Microsoft.Insights/workbooks/{name}/workbook")
    return 0


if __name__ == "__main__":
    sys.exit(main())