"""Figures and FP accounting for the policy report (no 160 MB handoff)."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fpce.replay.policy import sweep_window
from fpce.replay.report import (
    cost_alerted_successes,
    equilibrium_precision,
    napkin_fp_cost,
    net_range,
    plot_policy_comparison,
    sweep_thresholds,
)


CORNER = {
    "p_idle_watts": 100.0,
    "p_peak_watts": 300.0,
    "pue": 1.0,
    "wue_l_per_kwh": 1.0,
}


def test_napkin_is_n_times_one_window():
    one = sweep_window(np.array([0.4]), 10.0, [CORNER])
    got = napkin_fp_cost(20, dt_seconds=10.0, u_mean=0.4, corners=[CORNER])
    assert got["it_kwh_min"] == pytest.approx(20 * one["it_kwh_min"])
    assert got["water_liters_max"] == pytest.approx(20 * one["water_liters_max"])


def test_cost_alerted_successes_ignores_failures_and_zero_windows():
    handoff = pd.DataFrame(
        {
            "machine_id": ["m1", "m1", "m1"],
            "decision_time": [0.0, 0.0, 100.0],
            "event_end": [60.0, 60.0, 100.0],
            "failed": [0, 1, 0],
            "model_alert": [1, 1, 1],
        }
    )
    grid = pd.DataFrame(
        {
            "machine_id": ["m1"],
            "time_stamp": [0],
            "cpu_util_percent": [0.0],
        }
    )
    totals, stats = cost_alerted_successes(handoff, grid, [CORNER])
    assert stats["n_false_positives"] == 2
    assert stats["n_with_positive_window"] == 1
    expected = sweep_window(np.array([0.0]), 60.0, [CORNER])
    assert totals["it_kwh_min"] == pytest.approx(expected["it_kwh_min"])


def test_cost_alerted_successes_samples_and_scales():
    handoff = pd.DataFrame(
        {
            "machine_id": ["m1"] * 10,
            "decision_time": [0.0] * 10,
            "event_end": [60.0] * 10,
            "failed": [0] * 10,
            "model_alert": [1] * 10,
        }
    )
    grid = pd.DataFrame(
        {"machine_id": ["m1"], "time_stamp": [0], "cpu_util_percent": [0.0]}
    )
    full, _ = cost_alerted_successes(handoff, grid, [CORNER])
    sampled, stats = cost_alerted_successes(
        handoff, grid, [CORNER], sample_n=4, sample_seed=0
    )
    assert stats["n_sampled"] == 4
    assert stats["scale_factor"] == pytest.approx(10 / 4)
    assert sampled["it_kwh_min"] == pytest.approx(full["it_kwh_min"])


def test_cost_alerted_successes_uses_baseline_start():
    handoff = pd.DataFrame(
        {
            "machine_id": ["m1"],
            "decision_time": [0.0],
            "baseline_alert_time": [30.0],
            "event_end": [60.0],
            "failed": [0],
            "baseline_alert": [1],
        }
    )
    grid = pd.DataFrame(
        {
            "machine_id": ["m1", "m1"],
            "time_stamp": [0, 30],
            "cpu_util_percent": [0.0, 0.0],
        }
    )
    totals, _ = cost_alerted_successes(
        handoff,
        grid,
        [CORNER],
        alert_col="baseline_alert",
        start_col="baseline_alert_time",
    )
    expected = sweep_window(np.array([0.0]), 30.0, [CORNER])
    assert totals["it_kwh_min"] == pytest.approx(expected["it_kwh_min"])


def test_net_range_is_pessimistic_on_min():
    avoided = {k: 10.0 if k.endswith("_max") else 3.0 for k in (
        "it_kwh_min", "it_kwh_max", "facility_kwh_min", "facility_kwh_max",
        "water_liters_min", "water_liters_max",
    )}
    destroyed = {k: 4.0 if k.endswith("_max") else 1.0 for k in avoided}
    net = net_range(avoided, destroyed)
    assert net["it_kwh_min"] == pytest.approx(3.0 - 4.0)
    assert net["it_kwh_max"] == pytest.approx(10.0 - 1.0)


def test_equilibrium_precision_recovers_known_ratio():
    # 1 TP saves 10, 9 FPs destroy 1 each → need p > 1/(10+1) ≈ 0.0909
    avoided = {k: 10.0 for k in (
        "it_kwh_min", "it_kwh_max", "facility_kwh_min", "facility_kwh_max",
        "water_liters_min", "water_liters_max",
    )}
    destroyed = {k: 9.0 for k in avoided}
    got = equilibrium_precision(avoided, destroyed, n_costing_tp=1, n_fp=9)
    # avg_s=10, avg_l=1, p = 1/(10+1)
    assert got["precision_mid"] == pytest.approx(1.0 / 11.0)


def test_sweep_filters_without_recomputing():
    costing = pd.DataFrame(
        {
            "model_score": [0.4, 0.95, 0.999],
            "it_kwh_min": [1.0, 2.0, 3.0],
            "it_kwh_max": [1.0, 2.0, 3.0],
            "facility_kwh_min": [1.0, 2.0, 3.0],
            "facility_kwh_max": [1.0, 2.0, 3.0],
            "water_liters_min": [1.0, 2.0, 3.0],
            "water_liters_max": [1.0, 2.0, 3.0],
        }
    )
    fps = pd.DataFrame(
        {
            "model_score": [0.6, 0.95],
            "it_kwh_min": [10.0, 20.0],
            "it_kwh_max": [10.0, 20.0],
            "facility_kwh_min": [10.0, 20.0],
            "facility_kwh_max": [10.0, 20.0],
            "water_liters_min": [10.0, 20.0],
            "water_liters_max": [20.0, 20.0],
        }
    )
    out = sweep_thresholds(costing, fps, thresholds=(0.5, 0.9))
    row_05 = out["rows"][0]
    row_09 = out["rows"][1]
    assert row_05["n_costing_tp_caught"] == 2
    assert row_05["avoided"]["it_kwh_min"] == pytest.approx(5.0)
    assert row_05["destroyed"]["it_kwh_min"] == pytest.approx(30.0)
    assert row_09["n_costing_tp_caught"] == 2
    assert row_09["destroyed"]["it_kwh_min"] == pytest.approx(20.0)


def test_plot_policy_comparison_writes_png(tmp_path: Path):
    rng = {
        "it_kwh_min": 3.0,
        "it_kwh_max": 10.0,
        "water_liters_min": 1.0,
        "water_liters_max": 4.0,
        "facility_kwh_min": 3.0,
        "facility_kwh_max": 12.0,
    }
    destroyed_model = {**rng, "it_kwh_min": 60.0, "it_kwh_max": 180.0}
    destroyed_base = {**rng, "it_kwh_min": 6000.0, "it_kwh_max": 18000.0}
    summary = {
        "do_nothing": rng,
        "model_policy": {
            "avoided": rng,
            "destroyed": destroyed_model,
            "n_test_alerts": 23883,
            "n_alerts_in_time": 197,
        },
        "baseline_policy": {
            "avoided": rng,
            "destroyed": destroyed_base,
            "n_test_alerts": 1849651,
            "n_alerts_in_time": 203,
        },
    }
    path = tmp_path / "policy.png"
    plot_policy_comparison(summary, path)
    assert path.is_file()
    assert path.stat().st_size > 1000
