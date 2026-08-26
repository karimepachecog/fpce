"""Tests for Google cluster-data 2019 → attempt-level adapter."""

from __future__ import annotations

import pandas as pd
import pytest

from fpce.ingest.google_events import (
    EVICT,
    FAIL,
    FINISH,
    KILL,
    SCHEDULE,
    SUBMIT,
    build_attempts_sql,
    build_google_attempts,
)


def _run(events: pd.DataFrame, time_unit: str = "us") -> pd.DataFrame:
    import duckdb

    con = duckdb.connect()
    con.register("events", events)
    sql = build_attempts_sql(
        "events",
        columns=set(events.columns),
        time_unit=time_unit,
    )
    return con.execute(sql).df()


def _us(seconds: float) -> int:
    return int(seconds * 1_000_000)


def _events() -> pd.DataFrame:
    """Four keys covering the cases the old instance-collapse got wrong."""
    rows = [
        # collection 1: two attempts (EVICT then FINISH)
        (1, 0, _us(1), SUBMIT, 10.0, 0.5, "m1"),
        (1, 0, _us(2), SCHEDULE, 10.0, 0.5, "m1"),
        (1, 0, _us(12), EVICT, 10.0, 0.5, "m1"),
        (1, 0, _us(20), SCHEDULE, 10.0, 0.5, "m1"),
        (1, 0, _us(80), FINISH, 10.0, 0.5, "m1"),
        # collection 2: FAIL with no SCHEDULE (start imputed from SUBMIT)
        (2, 0, _us(1), SUBMIT, 4.0, 0.2, "m2"),
        (2, 0, _us(121), FAIL, 4.0, 0.2, "m2"),
        # collection 3: two terminals after one SCHEDULE → keep first (EVICT)
        (3, 0, _us(5), SCHEDULE, 8.0, 0.3, "m3"),
        (3, 0, _us(15), EVICT, 8.0, 0.3, "m3"),
        (3, 0, _us(25), FAIL, 8.0, 0.3, "m3"),
        # collection 4: single KILL attempt
        (4, 0, _us(5), SCHEDULE, 2.0, 0.1, "m4"),
        (4, 0, _us(25), KILL, 2.0, 0.1, "m4"),
        # collection 5: single FAIL with SCHEDULE, costable
        (5, 0, _us(2), SCHEDULE, 0.02, 0.01, "m5"),
        (5, 0, _us(122), FAIL, 0.02, 0.01, "m5"),
        # collection 6: FINISH (negative class)
        (6, 0, _us(1), SUBMIT, 0.01, 0.01, "m6"),
        (6, 0, _us(3), SCHEDULE, 0.01, 0.01, "m6"),
        (6, 0, _us(63), FINISH, 0.01, 0.01, "m6"),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "collection_id",
            "instance_index",
            "time",
            "type",
            "cpus_request",
            "memory_request",
            "machine_id",
        ],
    )


def test_multi_attempt_emits_two_rows_with_distinct_outcomes() -> None:
    out = _run(_events())
    rows = out.loc[out["collection_id"] == 1].sort_values("attempt_index")
    assert len(rows) == 2
    assert list(rows["attempt_index"].astype(int)) == [1, 2]
    assert rows.iloc[0]["terminal_type"] == "evicted"
    assert rows.iloc[1]["terminal_type"] == "succeeded"
    assert int(rows.iloc[0]["eligible_for_training"]) == 0
    assert int(rows.iloc[1]["eligible_for_training"]) == 1
    assert int(rows.iloc[0]["start_imputed"]) == 0


def test_missing_schedule_marks_start_imputed() -> None:
    out = _run(_events())
    row = out.loc[out["collection_id"] == 2].iloc[0]
    assert int(row["start_imputed"]) == 1
    assert int(row["failed"]) == 1
    assert int(row["attempt_index"]) == 1
    assert float(row["waste_window_seconds"]) == pytest.approx(120.0)
    assert int(row["eligible_for_costing"]) == 1


def test_two_terminals_on_same_schedule_collapse_to_first() -> None:
    out = _run(_events())
    rows = out.loc[out["collection_id"] == 3]
    assert len(rows) == 1
    assert rows.iloc[0]["terminal_type"] == "evicted"
    assert int(rows.iloc[0]["failed"]) == 0
    assert int(rows.iloc[0]["eligible_for_training"]) == 0


def test_fail_is_costable_and_trainable() -> None:
    out = _run(_events())
    row = out.loc[out["collection_id"] == 5].iloc[0]
    assert int(row["failed"]) == 1
    assert row["outcome"] == "failed"
    assert row["terminal_type"] == "failed"
    assert int(row["eligible_for_training"]) == 1
    assert int(row["eligible_for_costing"]) == 1
    assert float(row["waste_window_seconds"]) == pytest.approx(120.0)
    assert float(row["plan_cpu_frac"]) == pytest.approx(0.02)


def test_finish_is_negative_class() -> None:
    out = _run(_events())
    row = out.loc[out["collection_id"] == 6].iloc[0]
    assert int(row["failed"]) == 0
    assert row["outcome"] == "succeeded"
    assert int(row["eligible_for_training"]) == 1
    assert int(row["eligible_for_costing"]) == 0


def test_evict_and_kill_excluded_from_training() -> None:
    out = _run(_events())
    evicted = out.loc[out["collection_id"] == 3].iloc[0]
    killed = out.loc[out["collection_id"] == 4].iloc[0]
    assert evicted["terminal_type"] == "evicted"
    assert killed["terminal_type"] == "killed"
    assert int(evicted["eligible_for_training"]) == 0
    assert int(killed["eligible_for_training"]) == 0
    assert int(evicted["failed"]) == 0


def test_plan_columns_mapped() -> None:
    out = _run(_events())
    row = out.loc[out["collection_id"] == 5].iloc[0]
    assert float(row["plan_cpu"]) == pytest.approx(0.02)
    assert float(row["plan_mem"]) == pytest.approx(0.01)
    assert float(row["plan_cpu_frac"]) == pytest.approx(0.02)
    assert float(row["plan_mem_frac"]) == pytest.approx(0.01)


def test_pandas_wrapper_matches_sql() -> None:
    events = _events()
    via_sql = _run(events).sort_values(["collection_id", "attempt_index"]).reset_index(drop=True)
    via_wrap = (
        build_google_attempts(events)
        .sort_values(["collection_id", "attempt_index"])
        .reset_index(drop=True)
    )
    assert list(via_sql["terminal_type"]) == list(via_wrap["terminal_type"])
    assert len(via_sql) == len(via_wrap)


def test_stratified_sample_preserves_positive_rate(tmp_path) -> None:
    from fpce.ingest.google_events import write_stratified_trainable_sample

    n_pos, n_neg = 40, 160
    rows = []
    for i in range(n_pos + n_neg):
        rows.append(
            {
                "collection_id": i,
                "instance_index": 0,
                "attempt_index": 1,
                "failed": 1 if i < n_pos else 0,
                "eligible_for_training": 1,
                "plan_cpu_frac": 0.01,
            }
        )
    src = tmp_path / "attempts.parquet"
    pd.DataFrame(rows).to_parquet(src, index=False)
    out = tmp_path / "attempts_sample.parquet"
    payload = write_stratified_trainable_sample(src, out, n_rows=50, random_state=0)
    assert 30 <= payload["n_rows"] <= 70
    assert payload["source_positive_rate_pct"] == pytest.approx(20.0)
    assert abs(payload["positive_rate_pct"] - 20.0) < 8
