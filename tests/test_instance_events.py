"""Tests for instance-level events (no pipeline data required)."""

from __future__ import annotations

import pandas as pd
import pytest

from fpce.ingest.instance_events import build_instance_events


def _instances() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "instance_name": ["a", "b", "c", "d"],
            "task_name": ["T1", "T1", "T2", "T3"],
            "job_name": ["J1", "J1", "J2", "J3"],
            "task_type": [1, 1, 1, 1],
            "status": ["Failed", "Terminated", "Failed", "Running"],
            "start_time": [100, 100, 200, 300],
            "end_time": [400, 250, 0, 0],
            "machine_id": ["m1", "m1", "m2", "m2"],
            "seq_no": [1, 1, 1, 1],
            "total_seq_no": [1, 1, 1, 1],
            "cpu_avg": [10.0, 12.0, 8.0, 9.0],
            "cpu_max": [20.0, 22.0, 18.0, 19.0],
            "mem_avg": [0.1, 0.1, 0.1, 0.1],
            "mem_max": [0.2, 0.2, 0.2, 0.2],
        }
    )


def _tasks() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "task_name": ["T1", "T2", "T3"],
            "job_name": ["J1", "J2", "J3"],
            "plan_cpu": [50.0, 60.0, 70.0],
            "plan_mem": [0.4, 0.5, 0.6],
            "instance_num": [2, 1, 1],
            "start_time": [90, 200, 300],
            "end_time": [500, 450, 0],
        }
    )


def test_failed_with_end_time_is_costable() -> None:
    events = build_instance_events(_instances(), _tasks())
    row = events.loc[events["instance_name"] == "a"].iloc[0]
    assert int(row["failed"]) == 1
    assert row["outcome"] == "failed"
    assert int(row["decision_time"]) == 100
    assert int(row["event_end"]) == 400
    assert int(row["waste_window_seconds"]) == 300
    assert int(row["eligible_for_training"]) == 1
    assert int(row["eligible_for_costing"]) == 1


def test_terminated_is_negative_class() -> None:
    events = build_instance_events(_instances(), _tasks())
    row = events.loc[events["instance_name"] == "b"].iloc[0]
    assert int(row["failed"]) == 0
    assert row["outcome"] == "succeeded"
    assert int(row["eligible_for_training"]) == 1
    assert int(row["eligible_for_costing"]) == 0


def test_failed_end_time_zero_gets_task_upper_bound() -> None:
    events = build_instance_events(_instances(), _tasks())
    row = events.loc[events["instance_name"] == "c"].iloc[0]
    assert int(row["failed"]) == 1
    assert int(row["event_end"]) == 200
    assert int(row["waste_window_seconds"]) == 0
    assert int(row["eligible_for_costing"]) == 0
    assert int(row["waste_window_imputed"]) == 1
    assert int(row["waste_window_upper_bound_seconds"]) == 250  # task_end 450 - decision 200


def test_measured_costable_row_not_marked_imputed() -> None:
    events = build_instance_events(_instances(), _tasks())
    row = events.loc[events["instance_name"] == "a"].iloc[0]
    assert int(row["waste_window_imputed"]) == 0
    assert int(row["eligible_for_costing"]) == 1


def test_running_is_censored() -> None:
    events = build_instance_events(_instances(), _tasks())
    row = events.loc[events["instance_name"] == "d"].iloc[0]
    assert row["outcome"] == "censored"
    assert int(row["eligible_for_training"]) == 0
    assert int(row["eligible_for_costing"]) == 0


def test_task_plan_columns_joined() -> None:
    events = build_instance_events(_instances(), _tasks())
    assert float(events.loc[events["instance_name"] == "a", "plan_cpu"].iloc[0]) == 50.0
    assert float(events.loc[events["instance_name"] == "c", "instance_num"].iloc[0]) == 1.0
    assert float(events.loc[events["instance_name"] == "a", "plan_cpu_frac"].iloc[0]) == pytest.approx(
        50.0 / 100.0 / 96.0
    )
    assert float(events.loc[events["instance_name"] == "a", "plan_mem_frac"].iloc[0]) == pytest.approx(0.4)
