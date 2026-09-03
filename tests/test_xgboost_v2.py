"""XGB v2 winner selection is validation-only."""

import pytest

try:
    import xgboost  # noqa: F401
except Exception:
    pytest.skip(
        "xgboost OpenMP runtime (libomp) is not installed",
        allow_module_level=True,
    )

from fpce.model.xgboost_v2 import all_stalled, pick_winner


def test_pick_winner_prefers_pr_auc_then_fewer_fp() -> None:
    rows = [
        {
            "name": "A",
            "best_iteration": 0,
            "validation": {
                "pr_auc": 0.80,
                "roc_auc": 0.99,
                "threshold_0.5": {"false_positives": 10, "recall": 0.9, "f1": 0.2, "precision": 0.1},
            },
        },
        {
            "name": "B",
            "best_iteration": 12,
            "validation": {
                "pr_auc": 0.83,
                "roc_auc": 0.97,
                "threshold_0.5": {"false_positives": 50, "recall": 0.88, "f1": 0.3, "precision": 0.2},
            },
        },
        {
            "name": "C",
            "best_iteration": 3,
            "validation": {
                "pr_auc": 0.83,
                "roc_auc": 0.96,
                "threshold_0.5": {"false_positives": 20, "recall": 0.87, "f1": 0.25, "precision": 0.15},
            },
        },
    ]
    choice = pick_winner(rows)
    assert choice["winner"] == "C"  # same PR-AUC as B, fewer FP
    assert choice["ranking"][0] == "C"


def test_all_stalled() -> None:
    assert all_stalled(
        [{"best_iteration": 0}, {"best_iteration": 1}, {"best_iteration": 2}]
    )
    assert not all_stalled(
        [{"best_iteration": 0}, {"best_iteration": 40}]
    )
