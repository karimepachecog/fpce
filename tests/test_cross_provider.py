"""Tests for Alibaba → Google feature intersection and metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fpce.model.cross_provider import (
    CROSS_PROVIDER_EXCLUDE,
    ensure_plan_frac,
    evaluate_cross_provider,
    match_prevalence,
    shared_feature_columns,
    _with_retry_index,
)


def _alibaba() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "plan_cpu": [100, 100, 50, 50, 200, 200, 100, 50],
            "plan_mem": [0.3, 0.3, 0.2, 0.2, 0.8, 0.8, 0.3, 0.2],
            "seq_no": [1, 1, 1, 2, 1, 3, 1, 1],
            "machine_id": ["m_1"] * 8,
            "failed": [0, 0, 0, 0, 1, 1, 0, 0],
            "eligible_for_training": [1, 1, 1, 1, 1, 1, 1, 1],
            "start_time": [10, 20, 30, 40, 50, 200, 210, 220],
            "cpu_avg": [9, 9, 9, 9, 9, 9, 9, 9],
        }
    )


def _google() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "plan_cpu": [0.0104, 0.0208, 0.0052, 0.0208],
            "plan_mem": [0.3, 0.8, 0.2, 0.8],
            "attempt_index": [1, 1, 1, 2],
            "machine_id": [101, 102, 103, 104],
            "failed": [0, 1, 0, 1],
            "eligible_for_training": [1, 1, 1, 1],
            "terminal_type": ["succeeded", "failed", "succeeded", "failed"],
        }
    )


def _prepared(ali: pd.DataFrame, goog: pd.DataFrame):
    return (
        _with_retry_index(ensure_plan_frac(ali, "alibaba")),
        _with_retry_index(ensure_plan_frac(goog, "google")),
    )


def test_shared_features_are_allowlisted_intersection() -> None:
    ali, goog = _prepared(_alibaba(), _google())
    shared = shared_feature_columns(ali, goog)
    assert "plan_cpu_frac" in shared
    assert "plan_mem_frac" in shared
    assert "retry_index" in shared
    assert "cpu_avg" not in shared
    assert "plan_cpu" not in shared
    assert "machine_id" not in shared


def test_machine_id_excluded_even_when_present_on_both() -> None:
    ali, goog = _prepared(_alibaba(), _google())
    assert "machine_id" in ali.columns and "machine_id" in goog.columns
    shared = shared_feature_columns(ali, goog)
    assert "machine_id" not in shared
    assert "machine_id" in CROSS_PROVIDER_EXCLUDE


def test_alibaba_cpu_frac_is_hundredths_over_96_cores() -> None:
    ali = ensure_plan_frac(_alibaba(), "alibaba")
    assert float(ali.loc[0, "plan_cpu_frac"]) == pytest.approx(100 / 100 / 96)


def test_evaluate_cross_provider_runs() -> None:
    pytest.importorskip("sklearn")
    report = evaluate_cross_provider(
        _alibaba(),
        _google(),
        split_timestamp=100,
        max_train_rows=None,
    )
    assert "plan_cpu_frac" in report["features"]
    assert "machine_id" not in report["features"]
    assert "alibaba_test" in report
    assert "google" in report
    assert "lift" in report["google"]
    assert "roc_auc" in report["google"]
    assert report["google"]["at_0_5"]["confounded_by_prevalence"] is True
    assert "f1_degradation" not in report
    assert report["google"]["n"] == 4
    assert "google_equalized_prevalence" in report


def _separable(n_neg: int, n_pos: int, *, google: bool, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # Overlapping ranges plus label noise so thresholded F1 moves with prevalence.
    neg_cpu = rng.uniform(0.008, 0.018, size=n_neg)
    pos_cpu = rng.uniform(0.010, 0.024, size=n_pos)
    cpu = np.concatenate([neg_cpu, pos_cpu])
    failed = np.concatenate([np.zeros(n_neg, dtype=int), np.ones(n_pos, dtype=int)])
    flip = rng.random(len(failed)) < 0.25
    failed = np.where(flip, 1 - failed, failed)
    mem = np.where(failed == 1, 0.7, 0.25) + rng.normal(0, 0.08, size=len(failed))
    frame = pd.DataFrame(
        {
            "plan_cpu_frac": cpu,
            "plan_mem_frac": mem,
            "failed": failed,
            "eligible_for_training": 1,
            "start_time": np.arange(len(cpu)),
            "machine_id": np.arange(len(cpu)) if google else [f"m_{i}" for i in range(len(cpu))],
        }
    )
    if google:
        frame["attempt_index"] = 1
    else:
        frame["seq_no"] = 1
        frame["plan_cpu"] = cpu * 100 * 96
        frame["plan_mem"] = mem
    return frame


def test_roc_auc_stable_f1_moves_when_prevalence_shifts() -> None:
    pytest.importorskip("sklearn")
    alibaba = _separable(80, 20, google=False, seed=1)
    google = _separable(200, 200, google=True, seed=2)
    report = evaluate_cross_provider(
        alibaba, google, split_timestamp=None, max_train_rows=None
    )
    raw = report["google"]
    eq = report["google_equalized_prevalence"]
    assert raw["roc_auc"] is not None and eq["roc_auc"] is not None
    assert abs(raw["roc_auc"] - eq["roc_auc"]) < 0.15
    assert raw["at_0_5"]["f1"] != eq["at_calibrated_threshold"]["f1"]
    assert "lift" in raw and "lift" in eq


def test_match_prevalence_hits_target_rate() -> None:
    x = pd.DataFrame({"a": np.arange(1000)})
    y = np.concatenate([np.ones(400, dtype=int), np.zeros(600, dtype=int)])
    _, y_eq = match_prevalence(x, y, target_rate=0.05, random_state=0)
    rate = float(np.mean(y_eq))
    assert 0.03 < rate < 0.07
    assert y_eq.sum() < 400
