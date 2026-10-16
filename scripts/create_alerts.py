"""Create the e-mail alert on monitor findings: an action group + an Azure Monitor log alert.

    python scripts/create_alerts.py

ALERT_EMAIL comes from cloud.env (not committed). PUT = create or update, so re-running is safe.
Stateful (autoMitigate): one e-mail when a finding appears, one when it clears - not one per check.
"""
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

ENVIRONMENT = "itcs3556688020-env"
ACTION_GROUP = "pm25-oncall"
RULE = "pm25-monitor-finding"

# Same parsing as the dashboard query; one alert per (check, severity, mode).
QUERY = """
ContainerAppConsoleLogs_CL
| where Log_s startswith "{"
| extend e = parse_json(Log_s)
| where tostring(e.event) == "finding"
| summarize findings = count() by check = tostring(e.check), severity = tostring(e.severity), mode = tostring(e.mode)
""".strip()


def az(*args):
    r = subprocess.run([shutil.which("az"), *args, "-o", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"az {' '.join(args[:3])} failed:\n{r.stderr}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def put(url, body):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f)
    try:
        return az("rest", "--method", "put", "--url", url, "--body", f"@{f.name}")
    finally:
        os.unlink(f.name)


def main() -> int:
    config.load()
    rg, sub = os.environ["PROJECT_ID"], os.environ["AZURE_SUBSCRIPTION_ID"]
    email = os.environ.get("ALERT_EMAIL")
    if not email:
        sys.exit("set ALERT_EMAIL in cloud.env")

    env = az("containerapp", "env", "show", "-g", rg, "-n", ENVIRONMENT)
    cid = env["properties"]["appLogsConfiguration"]["logAnalyticsConfiguration"]["customerId"]
    ws = next(w for w in az("monitor", "log-analytics", "workspace", "list", "-g", rg)
              if w["customerId"] == cid)
    base = f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Insights"

    ag = put(f"{base}/actionGroups/{ACTION_GROUP}?api-version=2023-01-01", {
        "location": "Global",
        "properties": {"groupShortName": "pm25oncall", "enabled": True,
                       "emailReceivers": [{"name": "owner", "emailAddress": email,
                                           "useCommonAlertSchema": True}]}})

    put(f"{base}/scheduledQueryRules/{RULE}?api-version=2023-12-01", {
        "location": ws["location"],
        "kind": "LogAlert",
        "properties": {
            "displayName": "PM2.5 monitor finding",
            "description": "A monitoring check (stale data, feed outage, stuck sensor, service "
                           "health) reported a finding. See README: Monitoring.",
            "severity": 1,
            "enabled": True,
            "scopes": [ws["id"]],
            "evaluationFrequency": "PT15M",
            "windowSize": "PT2H",        # > the hourly job cadence + ingestion delay: no flapping
            "criteria": {"allOf": [{
                "query": QUERY,
                "timeAggregation": "Total",
                "metricMeasureColumn": "findings",
                "dimensions": [{"name": d, "operator": "Include", "values": ["*"]}
                               for d in ("check", "severity", "mode")],
                "operator": "GreaterThan",
                "threshold": 0,
                "failingPeriods": {"numberOfEvaluationPeriods": 1, "minFailingPeriodsToAlert": 1},
            }]},
            "autoMitigate": True,
            "actions": {"actionGroups": [ag["id"]]},
        }})
    print(f"alert '{RULE}' on {ws['name']} -> e-mail to {email} via action group {ACTION_GROUP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())