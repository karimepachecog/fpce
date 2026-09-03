"""Synthetic tests for scheduler policy accumulation (no 160 MB handoff)."""

import numpy as np
import pandas as pd
import pytest

from fpce.replay.policy import (
    alert_before_end,
    empty_range,
    filter_costing_failures,
    simulate_policy,
    sweep_window,
    utilization_fraction,
)


CORNER = {
    "p_idle_watts": 100.0,
    "p_peak_watts": 300.0,
    "pue": 1.0,
    "wue_l_per_kwh": 1.0,
}


def test_alert_before_end_requires_strict_lead():
    assert alert_before_end(1, 10.0, 20.0) is True
    assert alert_before_end(1, 20.0, 20.0) is False
    assert alert_before_end(1, 21.0, 20.0) is False
    assert alert_before_end(0, 10.0, 20.0) is False
    assert alert_before_end(1, np.nan, 20.0) is False


def test_sweep_window_matches_translate_idle():
    # 120 s at u=0 → 100 W * 120 s = 12000 W·s = 12000 / 3.6e6 kWh
    got = sweep_window(np.array([0.0, 0.0]), 120.0, [CORNER])
    assert got["it_kwh_min"] == pytest.approx(12000.0 / 3.6e6)
    assert got["it_kwh_max"] == pytest.approx(12000.0 / 3.6e6)
    assert got["water_liters_min"] == pytest.approx(12000.0 / 3.6e6)
    assert got["n_parameter_corners"] == 1


def test_sweep_window_empty_is_zero():
    got = sweep_window(np.array([]), 90.0, [CORNER])
    assert got == empty_range(n_corners=1)


def test_utilization_fraction_divides_percent():
    grid = pd.DataFrame(
        {
            "time_stamp": [0, 60, 120],
            "cpu_util_percent": [50.0, 100.0, 0.0],
        }
    )
    u = utilization_fraction(grid, 0, 120)
    assert u.tolist() == pytest.approx([0.5, 1.0])


def test_filter_drops_non_costing_and_successes():
    handoff = pd.DataFrame(
        {
            "failed": [1, 1, 0],
            "eligible_for_costing": [1, 0, 1],
        }
    )
    kept = filter_costing_failures(handoff)
    assert len(kept) == 1
    assert int(kept.iloc[0]["failed"]) == 1


def test_simulate_policy_model_vs_baseline_windows():
    """Model fires at admission; baseline fires 60 s later; constant 50% util."""
    handoff = pd.DataFrame(
        {
            "test_row_index": [0, 1],
            "instance_name": ["a", "b"],
            "machine_id": ["m1", "m1"],
            "decision_time": [0.0, 0.0],
            "event_end": [120.0, 120.0],
            "failed": [1, 1],
            "eligible_for_costing": [1, 1],
            "model_alert": [1, 0],
            "model_alert_time": [0.0, np.nan],
            "baseline_alert": [1, 1],
            "baseline_alert_time": [60.0, 60.0],
        }
    )
    # Role C already costed the full 120 s window (do-nothing).
    full = sweep_window(np.array([0.5, 0.5]), 120.0, [CORNER])
    costing = pd.DataFrame(
        {
            "test_row_index": [0, 1],
            "it_kwh_min": [full["it_kwh_min"], full["it_kwh_min"]],
            "it_kwh_max": [full["it_kwh_max"], full["it_kwh_max"]],
            "facility_kwh_min": [full["facility_kwh_min"], full["facility_kwh_min"]],
            "facility_kwh_max": [full["facility_kwh_max"], full["facility_kwh_max"]],
            "water_liters_min": [full["water_liters_min"], full["water_liters_min"]],
            "water_liters_max": [full["water_liters_max"], full["water_liters_max"]],
        }
    )
    grid = pd.DataFrame(
        {
            "machine_id": ["m1", "m1"],
            "time_stamp": [0, 60],
            "cpu_util_percent": [50.0, 50.0],
        }
    )

    result, summary = simulate_policy(handoff, costing, grid, [CORNER])
    assert len(result) == 2
    assert summary["n_costing_eligible_failures"] == 2
    assert summary["n_model_alerts_in_time"] == 1
    assert summary["n_baseline_alerts_in_time"] == 2

    expected_full = full["it_kwh_min"]
    expected_tail = sweep_window(np.array([0.5]), 60.0, [CORNER])["it_kwh_min"]

    # Row 0: model saves the full window; baseline saves the last 60 s.
    row0 = result.iloc[0]
    assert row0["do_nothing_it_kwh_min"] == pytest.approx(expected_full)
    assert row0["model_saved_it_kwh_min"] == pytest.approx(expected_full)
    assert row0["baseline_saved_it_kwh_min"] == pytest.approx(expected_tail)
    assert row0["model_minus_baseline_it_kwh_min"] == pytest.approx(
        expected_full - expected_tail
    )

    # Row 1: no model alert → no model savings; baseline still saves the tail.
    row1 = result.iloc[1]
    assert row1["model_saved_it_kwh_min"] == pytest.approx(0.0)
    assert row1["baseline_saved_it_kwh_min"] == pytest.approx(expected_tail)

    assert summary["do_nothing"]["it_kwh_min"] == pytest.approx(2 * expected_full)
    assert summary["model_policy"]["avoided"]["it_kwh_min"] == pytest.approx(expected_full)
    assert summary["baseline_policy"]["avoided"]["it_kwh_min"] == pytest.approx(
        2 * expected_tail
    )
    assert "post-registered" not in summary["threshold_note"]
    assert "test PR/FP" in summary["threshold_note"]
    assert summary["false_positives_excluded"]["n_test_fp_at_0.9"] == 20523
