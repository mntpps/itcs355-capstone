from pathlib import Path

import pandas as pd

from src import monitor

NOW = pd.Timestamp("2026-10-15 22:00", tz="Asia/Bangkok")
FIXTURES = Path(__file__).parent / "fixtures"


def station(loc, values, end=NOW):
    idx = pd.date_range(end=end, periods=len(values), freq="h")
    return pd.DataFrame({"location_id": loc, "time": idx, "pm25": values})


def frame(*parts):
    return pd.concat(parts, ignore_index=True)


def test_sensor_frozen_now_is_flagged_with_its_value_and_duration():
    hourly = frame(station("A", [20, 21, 22] + [15.0] * 10),
                   station("B", list(range(20, 33))))
    found = monitor.stuck_sensors(hourly, NOW)
    assert [f.details["location_id"] for f in found] == ["A"]
    assert found[0].details == {"location_id": "A", "value": 15.0, "hours": 10}


def test_sensor_frozen_yesterday_but_moving_now_is_not_flagged():
    hourly = station("A", [15.0] * 10 + [16, 18, 17])
    assert monitor.stuck_sensors(hourly, NOW) == []


def test_station_that_stopped_reporting_is_an_outage_not_a_frozen_sensor():
    hourly = station("A", [15.0] * 10, end=NOW - pd.Timedelta(hours=6))
    assert monitor.stuck_sensors(hourly, NOW) == []


def test_real_incident_bang_sue_is_caught_while_frozen():
    df = pd.read_csv(FIXTURES / "bang_sue_frozen_2023-09.csv")
    s = pd.Series(df["pm25"].values, index=pd.to_datetime(df["time"], utc=True)).sort_index()
    from src import checks
    frozen_at = s.index[checks.stuck_mask(s)][-1]           # replay up to a frozen hour
    hourly = pd.DataFrame({"location_id": "bang_sue", "time": s.index, "pm25": s.values})
    hourly = hourly[hourly["time"] <= frozen_at]
    found = monitor.stuck_sensors(hourly, frozen_at)
    assert len(found) == 1 and found[0].details["value"] == 15.0


def test_feed_outage_when_fewer_than_half_the_stations_report():
    hourly = frame(*[station(str(i), [20.0, 21.0]) for i in range(30)])
    assert monitor.feed_outage(hourly, NOW, expected_stations=77)[0].severity == "critical"
    hourly = frame(*[station(str(i), [20.0, 21.0]) for i in range(70)])
    assert monitor.feed_outage(hourly, NOW, expected_stations=77) == []


def test_stale_data_is_critical_after_one_day():
    assert monitor.stale_data(4)[0].check == "stale_data"
    assert monitor.stale_data(1) == []


def test_model_worse_than_persistence_is_flagged_only_with_enough_days():
    y = [20, 22, 24, 26, 28, 30, 32]
    yesterday = [19, 21, 23, 25, 27, 29, 31]          # persistence off by 1
    good = [20.5, 22, 24, 26, 28, 30, 32]
    bad = [25, 27, 29, 31, 33, 35, 37]                # off by 5
    assert monitor.model_vs_persistence(y, good, yesterday) == []
    assert monitor.model_vs_persistence(y, bad, yesterday)[0].severity == "warning"
    assert monitor.model_vs_persistence(y[:3], bad[:3], yesterday[:3]) == []