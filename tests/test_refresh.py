"""Daily refresh: which rows get replaced, and when nothing is published."""
import json

import pandas as pd
import pytest

from src import data_source, refresh

TZ = "Asia/Bangkok"


def targets(rows):
    return pd.DataFrame(rows, columns=["location_id", "date", "pm25", "source"])


def test_day_window_is_whole_bangkok_days_ending_before_today():
    start, end = refresh.day_window(10, pd.Timestamp("2026-10-16 23:10", tz=TZ))
    assert start == pd.Timestamp("2026-10-06 00:00", tz=TZ)
    assert end == pd.Timestamp("2026-10-16 00:00", tz=TZ)       # today is not finished


def test_day_window_uses_bangkok_date_not_utc():
    # 2026-10-16 01:30 in Bangkok is still 2026-10-15 in UTC
    _, end = refresh.day_window(1, pd.Timestamp("2026-10-15 18:30", tz="UTC"))
    assert end == pd.Timestamp("2026-10-16 00:00", tz=TZ)


def test_replace_window_replaces_same_keys_and_keeps_the_rest():
    old = targets([(1, "2026-10-09", 10.0, "24h"), (1, "2026-10-10", 11.0, "12h"),
                   (2, "2026-10-10", 20.0, "24h")])
    new = targets([(1, "2026-10-10", 15.0, "24h"), (1, "2026-10-11", 16.0, "24h")])
    out = refresh.replace_window(old, new, ["location_id", "date"])
    got = {(r.location_id, r.date): r.pm25 for r in out.itertuples()}
    assert got == {(1, "2026-10-09"): 10.0,       # outside the new rows: kept
                   (1, "2026-10-10"): 15.0,       # late hours arrived: replaced
                   (1, "2026-10-11"): 16.0,       # new day: added
                   (2, "2026-10-10"): 20.0}       # station silent now: old row kept
    assert list(out.columns) == list(old.columns)


def test_replace_window_with_nothing_new_returns_old():
    old = targets([(1, "2026-10-09", 10.0, "24h")])
    out = refresh.replace_window(old, targets([]), ["location_id", "date"])
    assert out.equals(old)


def _hours(day, n, value):
    times = pd.date_range(f"{day} 00:00", periods=n, freq="h", tz=TZ).tz_convert("UTC")
    return pd.DataFrame({"time_utc": times.strftime("%Y-%m-%dT%H:%M:%SZ"), "pm25": value})


def test_recent_targets_skips_failed_station_and_drops_days_outside_window(monkeypatch):
    # The daily rule itself is target.daily_target's job (and the refresh dry run matched
    # `ingest build` on every overlapping day). Here: failures, silent stations, the window.
    monkeypatch.setattr(refresh.target, "daily_target", lambda hourly: pd.DataFrame(
        {"date": ["2026-10-09", "2026-10-11", "2026-10-12"], "pm25": [1.0, 30.0, 99.0],
         "source": ["24h", "24h", "5h"]}))
    stations = pd.DataFrame({"location_id": [1, 2, 3], "sensor_id": [11, 22, 33]})
    start, end = pd.Timestamp("2026-10-10", tz=TZ), pd.Timestamp("2026-10-12", tz=TZ)

    def fetch(sensor_id, s, e):
        if sensor_id == 22:
            raise ConnectionError("timeout")
        if sensor_id == 33:
            return pd.DataFrame(columns=["time_utc", "pm25"])          # silent station
        return pd.DataFrame({"time_utc": ["2026-10-11T00:00:00Z"], "pm25": [30.0]})

    out, failed = refresh.recent_targets(stations, start, end, fetch=fetch)
    assert failed == [2]
    assert set(zip(out["location_id"], out["date"])) == {(1, "2026-10-11")}   # not before, not today


def test_check_refuses_fewer_rows_or_changed_columns():
    old = targets([(1, "2026-10-09", 10.0, "24h"), (1, "2026-10-10", 11.0, "24h")])
    assert refresh.check("t", old, old) == []
    assert "fewer rows" in refresh.check("t", old, old.head(1))[0]
    assert "columns changed" in refresh.check("t", old, old.drop(columns="source"))[0]


def test_serving_versions_prefers_manifest_then_pointer(tmp_path):
    (tmp_path / "targets.csv.dvc").write_text("outs:\n- md5: aaa\n  path: targets.csv\n")
    manifest = {"files": {"targets.csv": {"md5": "bbb"}}}
    got = data_source.serving_versions(["targets.csv"], "https://x/c", tmp_path,
                                       read=lambda uri, key: json.dumps(manifest))
    assert got == ({"targets.csv": "bbb"}, "manifest")
    got = data_source.serving_versions(["targets.csv"], "https://x/c", tmp_path,
                                       read=lambda uri, key: None)
    assert got == ({"targets.csv": "aaa"}, "pointer")


def test_ensure_replaces_an_older_cloud_copy(tmp_path):
    path = tmp_path / "targets.csv"
    path.write_text("old\n")
    new_md5 = __import__("hashlib").md5(b"new\n").hexdigest()

    def download(uri, key, dest):
        assert key == data_source.dvc_key(new_md5)
        dest.write_bytes(b"new\n")

    assert data_source.ensure(path, "https://x/c", tmp_path, download, want=new_md5) == new_md5
    assert path.read_text() == "new\n"


def test_ensure_without_blob_still_refuses_a_mismatch(tmp_path):
    path = tmp_path / "targets.csv"
    path.write_text("old\n")
    with pytest.raises(ValueError):
        data_source.ensure(path, None, tmp_path, want="0" * 32)