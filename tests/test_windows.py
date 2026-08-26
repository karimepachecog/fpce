"""Tests for host-grid as-of join at decision_time."""

from __future__ import annotations

import pandas as pd

from fpce.features.windows import join_host_at_decision


def test_join_takes_latest_grid_row_at_or_before_decision() -> None:
    events = pd.DataFrame(
        {
            "instance_name": ["a", "b"],
            "machine_id": ["m1", "m1"],
            "decision_time": [150, 250],
        }
    )
    grid = pd.DataFrame(
        {
            "machine_id": ["m1", "m1", "m1"],
            "time_stamp": [60, 120, 240],
            "cpu_util_percent": [10.0, 20.0, 90.0],
            "active_instances": [1, 2, 9],
        }
    )
    out = join_host_at_decision(events, grid, columns=["cpu_util_percent", "active_instances"])
    assert float(out.loc[out["instance_name"] == "a", "cpu_util_percent"].iloc[0]) == 20.0
    assert int(out.loc[out["instance_name"] == "a", "time_stamp"].iloc[0]) == 120
    assert float(out.loc[out["instance_name"] == "b", "cpu_util_percent"].iloc[0]) == 90.0
    assert int(out.loc[out["instance_name"] == "b", "time_stamp"].iloc[0]) == 240


def test_join_rejects_future_grid_rows() -> None:
    events = pd.DataFrame(
        {"instance_name": ["a"], "machine_id": ["m1"], "decision_time": [100]}
    )
    grid = pd.DataFrame(
        {
            "machine_id": ["m1"],
            "time_stamp": [200],
            "cpu_util_percent": [99.0],
        }
    )
    out = join_host_at_decision(events, grid, columns=["cpu_util_percent"])
    assert pd.isna(out.loc[0, "cpu_util_percent"])
    assert pd.isna(out.loc[0, "time_stamp"]) or out.loc[0, "time_stamp"] <= 100


def test_join_does_not_cross_machines() -> None:
    events = pd.DataFrame(
        {"instance_name": ["a"], "machine_id": ["m1"], "decision_time": [300]}
    )
    grid = pd.DataFrame(
        {
            "machine_id": ["m2", "m1"],
            "time_stamp": [300, 60],
            "cpu_util_percent": [77.0, 11.0],
        }
    )
    out = join_host_at_decision(events, grid, columns=["cpu_util_percent"])
    assert float(out.loc[0, "cpu_util_percent"]) == 11.0
    assert int(out.loc[0, "time_stamp"]) == 60
