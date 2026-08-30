"""Unit tests for the primary-rack first baseline (no pipeline parquets)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from fpce.features.assemble import PreparedSplit
from fpce.model.evaluate import select_f1_threshold, threshold_metrics
from fpce.model.train import (
    baseline_feature_columns,
    encode_task_type,
    prepare_design_matrices,
)


def test_baseline_feature_columns_drops_redundant_and_null_host() -> None:
    cols = [
        "machine_id",
        "task_type",
        "plan_cpu",
        "plan_cpu_frac",
        "plan_mem",
        "plan_mem_frac",
        "seq_no",
        "mem_gps",
        "mkpi",
        "cpu_util_percent",
    ]
    kept = baseline_feature_columns(cols)
    assert "plan_cpu_frac" in kept
    assert "plan_mem_frac" in kept
    assert "task_type" in kept
    assert "plan_cpu" not in kept
    assert "plan_mem" not in kept
    assert "machine_id" not in kept
    assert "mem_gps" not in kept
    assert "mkpi" not in kept


def test_task_type_unseen_becomes_nan() -> None:
    train = pd.Series([1, 3, 1, 11])
    test = pd.Series([1, 2, 3])
    tr, te, meta = encode_task_type(train, test)
    assert meta["n_train_categories"] == 3
    assert np.isnan(te[1])
    assert not np.isnan(te[0])
    assert not np.isnan(te[2])
    assert set(np.unique(tr[~np.isnan(tr)])).issubset({0.0, 1.0, 2.0})


def test_imputer_medians_from_train_only() -> None:
    prepared = PreparedSplit(
        feature_columns=["task_type", "plan_cpu_frac", "disk_io_percent", "machine_id"],
        target="failed",
        X_train=pd.DataFrame(
            {
                "task_type": [1, 1, 3],
                "plan_cpu_frac": [0.01, 0.02, np.nan],
                "disk_io_percent": [4.0, 6.0, 8.0],
                "machine_id": ["m1", "m1", "m2"],
            }
        ),
        X_test=pd.DataFrame(
            {
                "task_type": [1, 2],
                "plan_cpu_frac": [np.nan, 0.99],
                "disk_io_percent": [100.0, np.nan],
                "machine_id": ["m9", "m9"],
            }
        ),
        y_train=pd.Series([0, 1, 0]),
        y_test=pd.Series([0, 1]),
        split_timestamp=300,
        split_column="start_time",
    )
    x_train, x_test, y_train, y_test, ordered, meta = prepare_design_matrices(prepared)
    assert ordered[0] == "task_type"
    assert "machine_id" not in ordered
    medians = meta["numeric_imputer"]["medians"]
    assert medians["plan_cpu_frac"] == 0.015
    # train-only median for disk_io is 6; test NaN is filled with 6, not 100.
    disk_idx = ordered.index("disk_io_percent")
    assert x_test[1, disk_idx] == 6.0
    cpu_idx = ordered.index("plan_cpu_frac")
    assert x_test[0, cpu_idx] == 0.015
    assert list(y_train) == [0, 1, 0]
    assert list(y_test) == [0, 1]


def test_threshold_selected_on_train_scores_only() -> None:
    y_train = np.array([0, 0, 1, 1])
    train_scores = np.array([0.1, 0.2, 0.8, 0.9])
    y_test = np.array([1, 1, 1, 1])
    test_scores = np.array([0.01, 0.02, 0.03, 0.04])
    chosen = select_f1_threshold(y_train, train_scores)
    test_at_chosen = threshold_metrics(y_test, test_scores, chosen["threshold"])
    # If the rule had peeked at test, a very low threshold would look perfect.
    assert chosen["threshold"] >= 0.2
    assert test_at_chosen["recall"] == 0.0
