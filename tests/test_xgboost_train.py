"""Tests for XGBoost candidate helpers (no 13M parquets)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from fpce.model.train import matrices_for_slices, temporal_val_mask


def test_temporal_val_mask_is_ordered_and_uses_train_only() -> None:
    t = pd.Series([10, 20, 30, 40, 50, 80, 90])
    inner, val_ts = temporal_val_mask(t, split_timestamp=100, train_time_frac=0.8)
    assert val_ts == int(10 + 0.8 * (100 - 10))
    assert inner.tolist() == [True, True, True, True, True, True, False]
    # no shuffle: last times are validation
    assert t.loc[~inner].min() >= val_ts


def test_imputer_and_encoder_fit_on_inner_only() -> None:
    X = pd.DataFrame(
        {
            "task_type": [1, 1, 3, 1, 11],
            "plan_cpu_frac": [0.01, np.nan, 0.03, 0.02, 0.04],
            "disk_io_percent": [4.0, 6.0, 8.0, 10.0, np.nan],
            "machine_id": ["m"] * 5,
            "plan_cpu": [50] * 5,
        }
    )
    y = pd.Series([0, 0, 1, 0, 0])
    X_test = pd.DataFrame(
        {
            "task_type": [2, 1],
            "plan_cpu_frac": [np.nan, 0.9],
            "disk_io_percent": [np.nan, 1.0],
            "machine_id": ["z", "z"],
            "plan_cpu": [1, 1],
        }
    )
    inner = np.array([True, True, True, False, False])
    x_in, x_va, x_te, y_in, y_va, y_te, names, meta = matrices_for_slices(
        X, y, inner, X_test, pd.Series([0, 1])
    )
    assert names[0] == "task_type"
    assert "machine_id" not in names
    assert "plan_cpu" not in names
    assert y_in.tolist() == [0, 0, 1]
    assert y_va.tolist() == [0, 0]
    # median of inner plan_cpu_frac [0.01, nan, 0.03] = 0.02
    cpu_idx = names.index("plan_cpu_frac")
    assert x_te[0, cpu_idx] == 0.02
    # task_type=2 unseen → NaN
    assert np.isnan(x_te[0, 0])
    assert meta["scale_pos_weight"] == 2.0
    assert meta["numeric_imputer"]["statistics_from"].startswith("inner train")
