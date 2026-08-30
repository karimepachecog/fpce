"""Classification metrics for Role B (no lead-time, no costing)."""

from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)


def confusion_counts(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, int]:
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "true_negatives": int(tn),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        "true_positives": int(tp),
    }


def ranking_metrics(y_true: np.ndarray, scores: np.ndarray) -> dict:
    """Threshold-free metrics. Constant scores → ROC-AUC 0.5, PR-AUC ≈ prevalence."""
    y_true = np.asarray(y_true, dtype=int)
    scores = np.asarray(scores, dtype=float)
    out: dict = {
        "n": int(len(y_true)),
        "n_positive": int(y_true.sum()),
        "positive_rate": float(y_true.mean()) if len(y_true) else 0.0,
    }
    if len(y_true) == 0 or y_true.min() == y_true.max():
        out["roc_auc"] = None
        out["pr_auc"] = None
        out["average_precision"] = None
        return out
    roc = float(roc_auc_score(y_true, scores))
    ap = float(average_precision_score(y_true, scores))
    out["roc_auc"] = roc
    out["pr_auc"] = ap
    out["average_precision"] = ap
    return out


def threshold_metrics(
    y_true: np.ndarray,
    scores: np.ndarray,
    threshold: float,
) -> dict:
    y_true = np.asarray(y_true, dtype=int)
    scores = np.asarray(scores, dtype=float)
    pred = (scores >= threshold).astype(int)
    counts = confusion_counts(y_true, pred)
    return {
        "threshold": float(threshold),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "accuracy": float(accuracy_score(y_true, pred)),
        "accuracy_not_useful": True,
        **counts,
        "confusion_matrix": {
            "labels": [0, 1],
            "matrix": [
                [counts["true_negatives"], counts["false_positives"]],
                [counts["false_negatives"], counts["true_positives"]],
            ],
        },
    }


def operational_metrics(
    y_true: np.ndarray,
    scores: np.ndarray,
    threshold: float,
) -> dict:
    """Threshold metrics plus alert volume (evaluation only)."""
    row = threshold_metrics(y_true, scores, threshold)
    n = int(len(y_true))
    n_alerts = row["true_positives"] + row["false_positives"]
    tp = row["true_positives"]
    fp = row["false_positives"]
    row["n_alerts"] = n_alerts
    row["fp_per_tp"] = (float(fp) / tp) if tp else None
    row["alert_rate"] = (n_alerts / n) if n else 0.0
    row["alert_rate_pct"] = 100.0 * row["alert_rate"]
    return row


def select_operating_point(
    y_true: np.ndarray,
    scores: np.ndarray,
    *,
    min_recall: float | None = None,
    min_precision: float | None = None,
) -> dict | None:
    """Descriptive point on the PR curve (typically test). Does not refit."""
    y_true = np.asarray(y_true, dtype=int)
    scores = np.asarray(scores, dtype=float)
    if min_recall is None and min_precision is None:
        raise ValueError("provide min_recall and/or min_precision")
    precision, recall, thresholds = precision_recall_curve(y_true, scores)
    # Last PR point has no threshold (precision=positive_rate at recall=1 is first;
    # sklearn ends with recall=0, precision=1).
    if len(thresholds) == 0:
        return None
    prec = precision[:-1]
    rec = recall[:-1]
    mask = np.ones(len(thresholds), dtype=bool)
    if min_recall is not None:
        mask &= rec >= min_recall
    if min_precision is not None:
        mask &= prec >= min_precision
    if not mask.any():
        return None
    idx_all = np.flatnonzero(mask)
    if min_recall is not None and min_precision is None:
        pick = idx_all[np.argmax(prec[mask])]
        objective = "max precision subject to recall constraint"
    elif min_precision is not None and min_recall is None:
        pick = idx_all[np.argmax(rec[mask])]
        objective = "max recall subject to precision constraint"
    else:
        f1 = 2 * prec * rec / np.clip(prec + rec, 1e-12, None)
        pick = idx_all[np.argmax(f1[mask])]
        objective = "max F1 subject to both constraints"
    threshold = float(thresholds[pick])
    row = operational_metrics(y_true, scores, threshold)
    row["objective"] = objective
    row["min_recall"] = min_recall
    row["min_precision"] = min_precision
    return row


def select_f1_threshold(
    y_true: np.ndarray,
    scores: np.ndarray,
    *,
    n_grid: int = 99,
) -> dict:
    """Pick a threshold by maximising F1 on *this* split only (use train)."""
    y_true = np.asarray(y_true, dtype=int)
    scores = np.asarray(scores, dtype=float)
    if len(y_true) == 0 or y_true.min() == y_true.max():
        return {"threshold": 0.5, "train_f1": 0.0, "n_candidates": 0}
    quantiles = np.linspace(0.01, 0.99, n_grid)
    candidates = np.unique(np.quantile(scores, quantiles))
    best_t, best_f1 = 0.5, -1.0
    for threshold in candidates:
        pred = (scores >= threshold).astype(int)
        score = float(f1_score(y_true, pred, zero_division=0))
        if score > best_f1:
            best_t, best_f1 = float(threshold), score
    return {
        "threshold": best_t,
        "train_f1": best_f1,
        "n_candidates": int(len(candidates)),
        "rule": "max F1 on train scores; test labels unused",
    }
