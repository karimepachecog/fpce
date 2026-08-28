"""Unit tests for threshold sweep helpers (synthetic scores)."""

from __future__ import annotations

import numpy as np
import pytest

from fpce.model.evaluate import operational_metrics, select_operating_point
from fpce.model.threshold_analysis import score_percentiles, sweep_thresholds


def test_operational_metrics_alert_volume() -> None:
    y = np.array([0, 0, 1, 1])
    scores = np.array([0.1, 0.6, 0.4, 0.9])
    row = operational_metrics(y, scores, 0.5)
    assert row["true_positives"] == 1
    assert row["false_positives"] == 1
    assert row["n_alerts"] == 2
    assert row["fp_per_tp"] == 1.0
    assert row["alert_rate"] == 0.5


def test_operating_point_max_precision_at_recall() -> None:
    y = np.array([0, 0, 0, 1, 1, 1])
    scores = np.array([0.1, 0.2, 0.55, 0.6, 0.8, 0.9])
    point = select_operating_point(y, scores, min_recall=2 / 3)
    assert point is not None
    assert point["recall"] >= 2 / 3 - 1e-9
    assert point["true_positives"] >= 2


def test_precision_constraint_can_be_impossible() -> None:
    y = np.array([0, 0, 0, 1])
    scores = np.array([0.9, 0.8, 0.7, 0.6])
    assert select_operating_point(y, scores, min_precision=0.99) is None


def test_sweep_and_percentiles() -> None:
    y = np.array([0, 0, 1, 1])
    scores = np.array([0.05, 0.15, 0.85, 0.95])
    rows = sweep_thresholds(y, scores, thresholds=(0.5, 0.9))
    assert rows[0]["threshold"] == 0.5
    assert rows[1]["true_positives"] == 1
    dist = score_percentiles(y, scores)
    assert dist["positives"]["p50"] == pytest.approx(0.9)
    assert dist["negatives"]["p50"] == pytest.approx(0.1)
