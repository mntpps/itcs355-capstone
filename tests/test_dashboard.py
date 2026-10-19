"""The dashboard definition: every tile is a valid, distinct query over a logged event."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "create_dashboard", Path(__file__).resolve().parents[1] / "scripts" / "create_dashboard.py")
dash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dash)

LOGGED = {"request", "model_performance", "refresh", "finding", "retrain"}


def test_every_tile_reads_an_event_we_actually_log():
    for t in dash.TILES:
        assert t["query"].startswith(dash.LOGS)
        assert any(f'"event": "{e}"' in t["query"] for e in LOGGED), t["title"]
        assert t["viz"] in {"timechart", "barchart", "table"}


def test_workbook_is_valid_json_with_one_item_per_tile():
    wb = json.loads(json.dumps(dash.workbook("/subscriptions/x/workspaces/y")))
    tiles = [i for i in wb["items"] if i["type"] == 3]
    assert len(tiles) == len(dash.TILES) == 6
    assert len({i["name"] for i in wb["items"]}) == len(wb["items"])
    assert all(i["content"]["crossComponentResources"] == ["/subscriptions/x/workspaces/y"] for i in tiles)