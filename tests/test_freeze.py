"""Handoff frame construction for the frozen Role B HistGB (no pipeline parquets)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from fpce.model.baseline import reactive_fire_time, train_runtime_medians
from fpce.model.freeze import build_handoff_frame, handoff_summary


def test_handoff_one_row_per_test_event() -> None:
    train = pd.DataFrame(
        {
            "failed": [0, 0],
            "task_type": [1, 1],
            "waste_window_seconds": [10.0, 30.0],
        }
    )
    test = pd.DataFrame(
        {
            "instance_name": ["a", "b", "c"],
            "task_name": ["t", "t", "t"],
            "job_name": ["j", "j", "j"],
            "machine_id": ["m1", "m1", "m2"],
            "start_time": [100, 200, 300],
            "seq_no": [1, 2, 1],
            "total_seq_no": [1, 2, 1],
            "task_type": [1, 1, 1],
            "decision_time": [100.0, 200.0, 300.0],
            "event_end": [150.0, 200.0, 280.0],
            "end_time": [150.0, 0.0, 280.0],
            "failed": [1, 1, 0],
            "waste_window_seconds": [50.0, 0.0, 0.0],
            "waste_window_imputed": [0, 1, 0],
            "eligible_for_costing": [0, 0, 0],
        }
    )
    scores = np.array([0.95, 0.2, 0.99])
    medians = train_runtime_medians(train)
    frame = build_handoff_frame(test, scores, medians, threshold=0.9)
    assert len(frame) == 3
    assert frame["model_alert"].tolist() == [1, 0, 1]
    assert float(frame.loc[0, "model_lead_time_seconds"]) == 50.0
    assert pd.isna(frame.loc[1, "model_lead_time_seconds"])
    assert int(frame.loc[1, "has_positive_measurable_window"]) == 0
    assert int(frame.loc[0, "has_positive_measurable_window"]) == 1
    # Retry seq_no>=2 fires at decision_time; equal to event_end is not "before".
    assert int(frame.loc[1, "baseline_alert"]) == 0
    summary = handoff_summary(frame, threshold=0.9)
    assert summary["n_failures"] == 2
    assert summary["n_failures_with_positive_measurable_window"] == 1
    assert summary["n_failures_anticipated_by_model"] == 1


def test_reactive_fire_time_used_for_baseline_columns() -> None:
    train = pd.DataFrame(
        {"failed": [0], "task_type": [3], "waste_window_seconds": [40.0]}
    )
    test = pd.DataFrame(
        {
            "start_time": [0],
            "machine_id": ["m"],
            "decision_time": [0.0],
            "event_end": [100.0],
            "failed": [1],
            "seq_no": [1],
            "task_type": [3],
        }
    )
    fire = reactive_fire_time(test, train_runtime_medians(train))
    assert float(fire.iloc[0]) == 40.0
    frame = build_handoff_frame(
        test, np.array([0.91]), train_runtime_medians(train), threshold=0.9
    )
    assert float(frame.loc[0, "baseline_alert_time"]) == 40.0
    assert int(frame.loc[0, "baseline_alert"]) == 1
    assert float(frame.loc[0, "delta_lead_time_seconds"]) == 40.0
