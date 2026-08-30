"""Lead-time and reactive-baseline helpers (no pipeline parquets)."""

from __future__ import annotations

import pandas as pd

from fpce.model.baseline import reactive_fire_time, train_runtime_medians
from fpce.model.lead_time import alert_before_failure, lead_seconds


def test_lead_only_if_alert_strictly_before_failure() -> None:
    alert = pd.Series([100.0, 200.0, 300.0, 50.0])
    end = pd.Series([150.0, 200.0, 250.0, 80.0])
    lead = lead_seconds(alert, end)
    assert float(lead.iloc[0]) == 50.0
    assert pd.isna(lead.iloc[1])  # equal: not before
    assert pd.isna(lead.iloc[2])  # after failure
    assert float(lead.iloc[3]) == 30.0
    detected = alert_before_failure(alert, end)
    assert detected.tolist() == [True, False, False, True]


def test_reactive_retry_fires_at_admission() -> None:
    train = pd.DataFrame(
        {
            "failed": [0, 0, 0],
            "task_type": [1, 1, 3],
            "waste_window_seconds": [100.0, 200.0, 50.0],
        }
    )
    medians = train_runtime_medians(train)
    assert float(medians.loc[1]) == 150.0
    events = pd.DataFrame(
        {
            "seq_no": [1, 2, 1],
            "task_type": [1, 1, 9],
            "decision_time": [0.0, 10.0, 0.0],
        }
    )
    fire = reactive_fire_time(events, medians)
    assert float(fire.iloc[0]) == 150.0  # runtime median task 1
    assert float(fire.iloc[1]) == 10.0  # retry at admission beats runtime
    assert float(fire.iloc[2]) == 100.0  # global fallback median of successes
