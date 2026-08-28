"""Unit tests for primary training-matrix assembly (no pipeline parquets)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fpce.contracts import load_feature_contract
from fpce.features.assemble import (
    assemble_feature_frame,
    describe_prepared,
    prepare_rack_training,
    select_feature_columns,
    split_prepared,
)
from fpce.io import write_parquet


def _events() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "eligible_for_training": [1, 1, 0, 1],
            "failed": [0, 1, 0, 0],
            "start_time": [100, 200, 150, 400],
            "decision_time": [100, 200, 150, 400],
            "machine_id": ["m1", "m1", "m1", "m1"],
            "seq_no": [1, 2, 1, 1],
            "total_seq_no": [1, 2, 1, 1],
            "plan_cpu": [50.0, 50.0, 50.0, 80.0],
            "plan_mem": [0.3, 0.3, 0.3, 0.4],
            "plan_cpu_frac": [0.005, 0.005, 0.005, 0.008],
            "plan_mem_frac": [0.3, 0.3, 0.3, 0.4],
            "instance_num": [2.0, 2.0, 2.0, 2.0],
            "task_type": [1, 1, 1, 1],
            "cpu_avg": [9.0, 9.0, 9.0, 9.0],
            "status": ["Terminated", "Failed", "Running", "Terminated"],
        }
    )


def _grid() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "machine_id": ["m1", "m1", "m1"],
            "time_stamp": [60, 180, 500],
            "cpu_util_percent": [10.0, 40.0, 99.0],
            "mem_util_percent": [50.0, 55.0, 90.0],
            "mem_gps": [pd.NA, pd.NA, pd.NA],
            "mkpi": [pd.NA, pd.NA, pd.NA],
            "net_in": [1.0, 2.0, 3.0],
            "net_out": [1.0, 2.0, 3.0],
            "disk_io_percent": [1.0, 2.0, 3.0],
            "active_instances": [1, 4, 8],
            "failure_within_horizon": [0, 1, 1],
        }
    )


def test_select_feature_columns_keeps_allow_list_only() -> None:
    contract = load_feature_contract()
    frame = _events()[["plan_cpu", "cpu_avg", "failed"]].copy()
    frame["cpu_util_percent"] = 1.0
    kept = select_feature_columns(frame, contract)
    assert kept == ["plan_cpu", "cpu_util_percent"]
    contract.assert_no_leakage(kept)


def test_asof_join_ignores_future_host_row() -> None:
    contract = load_feature_contract()
    events = _events()
    events = events.loc[events["eligible_for_training"] == 1]
    joined = assemble_feature_frame(events, _grid(), contract)
    row_early = joined.loc[joined["start_time"] == 100].iloc[0]
    row_mid = joined.loc[joined["start_time"] == 200].iloc[0]
    row_late = joined.loc[joined["start_time"] == 400].iloc[0]
    assert float(row_early["cpu_util_percent"]) == 10.0
    assert float(row_mid["cpu_util_percent"]) == 40.0
    assert float(row_late["cpu_util_percent"]) == 40.0
    assert "failure_within_horizon" not in select_feature_columns(joined, contract)
    assert "cpu_avg" not in joined.columns or "cpu_avg" not in select_feature_columns(joined, contract)


def test_time_split_not_random(tmp_path: Path) -> None:
    contract = load_feature_contract()
    events = _events().drop(columns=["cpu_avg", "status"])
    write_parquet(events, tmp_path / "instance_events.parquet")
    write_parquet(_grid(), tmp_path / "time_grid.parquet")
    (tmp_path / "split.json").write_text(
        '{"split_timestamp": 300, "grid_path": "x", "instance_events_path": "y"}',
        encoding="utf-8",
    )
    prepared = prepare_rack_training(
        events_path=tmp_path / "instance_events.parquet",
        grid_path=tmp_path / "time_grid.parquet",
        split_path=tmp_path / "split.json",
    )
    assert prepared.target == "failed"
    assert prepared.y_train.tolist() == [0, 1]
    assert prepared.y_test.tolist() == [0]
    assert prepared.X_train.shape[0] == 2
    assert prepared.X_test.shape[0] == 1
    report = describe_prepared(prepared)
    assert report["train"]["n"] == 2
    assert report["test"]["n_positive"] == 0
    assert "cpu_util_percent" in prepared.feature_columns
    assert "failed" not in prepared.feature_columns
