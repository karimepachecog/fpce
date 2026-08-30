"""Primary-rack XGBoost candidate vs the frozen HistGB baseline.

Does not change the time split or feature contract. Thresholds are chosen on a
temporal slice of train (never test). HistGB reports on disk are not overwritten.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fpce.config import REPORTS_DIR
from fpce.features.assemble import prepare_primary_training
from fpce.model.evaluate import (
    operational_metrics,
    ranking_metrics,
    select_f1_threshold,
    select_operating_point,
)
from fpce.model.baseline import reactive_fire_time, train_runtime_medians
from fpce.model.lead_time import (
    FIGURES_DIR,
    alert_before_failure,
    attach_scores,
    lead_seconds,
    load_split_events,
    summarize_lead,
)
from fpce.model.threshold_analysis import load_test_scores, save_test_scores
from fpce.model.train import matrices_for_slices, temporal_val_mask

try:
    from xgboost import XGBClassifier
except ImportError as exc:  # pragma: no cover
    XGBClassifier = None  # type: ignore[misc, assignment]
    _XGB_IMPORT_ERROR = exc
else:
    _XGB_IMPORT_ERROR = None

HGB_SCORES = REPORTS_DIR / "primary_hgb_test_scores.npz"
HGB_WORKING_THRESHOLD = 0.9
XGB_SCORES = REPORTS_DIR / "primary_xgb_test_scores.npz"
XGB_BASELINE_JSON = REPORTS_DIR / "primary_xgb_baseline.json"
XGB_THRESHOLDS_JSON = REPORTS_DIR / "primary_xgb_thresholds.json"
XGB_LEAD_JSON = REPORTS_DIR / "primary_xgb_lead_time.json"

XGB_PARAMS = {
    "objective": "binary:logistic",
    "eval_metric": "aucpr",
    "n_estimators": 300,
    "learning_rate": 0.05,
    "max_depth": 6,
    "min_child_weight": 20,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "random_state": 0,
    "n_jobs": -1,
    "tree_method": "hist",
    "enable_categorical": True,
    "early_stopping_rounds": 30,
}
TRAIN_TIME_FRAC = 0.8


def _require_xgb() -> None:
    if XGBClassifier is None:
        raise ImportError(
            "xgboost is required. Install with: pip install 'xgboost>=2.0' "
            f"({_XGB_IMPORT_ERROR})"
        )


def as_xgb_frame(x: np.ndarray, names: list[str], n_categories: int) -> pd.DataFrame:
    df = pd.DataFrame(x, columns=names)
    df["task_type"] = pd.Categorical(
        df["task_type"], categories=list(range(n_categories))
    )
    return df


def fit_xgboost(
    x_inner: np.ndarray,
    y_inner: np.ndarray,
    x_val: np.ndarray,
    y_val: np.ndarray,
    *,
    feature_names: list[str],
    n_categories: int,
    scale_pos_weight: float,
) -> tuple[object, dict]:
    _require_xgb()
    params = dict(XGB_PARAMS)
    params["scale_pos_weight"] = float(scale_pos_weight)
    model = XGBClassifier(**params)
    X_tr = as_xgb_frame(x_inner, feature_names, n_categories)
    X_va = as_xgb_frame(x_val, feature_names, n_categories)
    model.fit(X_tr, y_inner, eval_set=[(X_va, y_val)], verbose=False)
    used = dict(params)
    used["sklearn_estimator"] = "XGBClassifier"
    used["best_iteration"] = int(getattr(model, "best_iteration", params["n_estimators"]))
    used["best_score"] = getattr(model, "best_score", None)
    if used["best_score"] is not None:
        used["best_score"] = float(used["best_score"])
    return model, used


def _predict_proba(model, x: np.ndarray, names: list[str], n_categories: int) -> np.ndarray:
    return model.predict_proba(as_xgb_frame(x, names, n_categories))[:, 1]


def select_thresholds_on_val(y_val: np.ndarray, proba_val: np.ndarray) -> dict:
    f1 = select_f1_threshold(y_val, proba_val)
    rec90 = select_operating_point(y_val, proba_val, min_recall=0.90)
    rec85 = select_operating_point(y_val, proba_val, min_recall=0.85)
    prec10 = select_operating_point(y_val, proba_val, min_precision=0.10)
    # Operational: cut FP vs 0.5 while keeping recall >= 0.85 on validation.
    operational = prec10 or rec85 or f1
    op_threshold = (
        operational["threshold"] if isinstance(operational, dict) else 0.5
    )
    at_05 = operational_metrics(y_val, proba_val, 0.5)
    return {
        "split": "temporal validation (inner train unused for this choice)",
        "max_f1": f1,
        "recall_ge_0.90_max_precision": rec90,
        "recall_ge_0.85_max_precision": rec85,
        "precision_ge_0.10_max_recall": prec10,
        "operational": {
            "rule": (
                "max recall on validation subject to precision >= 0.10 "
                "(reduces FP vs a high-recall cut). Falls back to recall>=0.85 "
                "then max-F1 if the precision constraint is infeasible."
            ),
            "threshold": float(op_threshold),
            "validation_metrics": operational_metrics(y_val, proba_val, op_threshold),
        },
        "validation_at_0.5": at_05,
        "note": "Test labels were not used to pick any threshold.",
    }


def _row(metrics: dict) -> dict:
    return {
        "pr_auc": metrics.get("pr_auc"),
        "roc_auc": metrics.get("roc_auc"),
        "precision": metrics.get("precision"),
        "recall": metrics.get("recall"),
        "f1": metrics.get("f1"),
        "accuracy": metrics.get("accuracy"),
        "true_positives": metrics.get("true_positives"),
        "false_positives": metrics.get("false_positives"),
        "false_negatives": metrics.get("false_negatives"),
        "true_negatives": metrics.get("true_negatives"),
        "n_alerts": metrics.get("n_alerts"),
        "fp_per_tp": metrics.get("fp_per_tp"),
        "alert_rate_pct": metrics.get("alert_rate_pct"),
        "threshold": metrics.get("threshold"),
    }


def comparison_table(hgb: dict, xgb: dict) -> dict:
    keys = [
        "pr_auc",
        "roc_auc",
        "precision",
        "recall",
        "f1",
        "true_positives",
        "false_positives",
        "false_negatives",
        "fp_per_tp",
        "alert_rate_pct",
    ]
    return {k: {"hist_gb": hgb.get(k), "xgboost": xgb.get(k)} for k in keys}


def _positive_window_mask(failures: pd.DataFrame) -> pd.Series:
    end = pd.to_numeric(failures["event_end"], errors="coerce")
    start = pd.to_numeric(failures["decision_time"], errors="coerce")
    return end.notna() & (end > start)


def _detect(failures: pd.DataFrame, proba: np.ndarray, threshold: float) -> tuple[pd.Series, pd.Series]:
    alert = failures["decision_time"].where(np.asarray(proba) >= threshold, pd.NA)
    detected = alert_before_failure(alert, failures["event_end"])
    lead = lead_seconds(alert, failures["event_end"])
    return detected, lead


def lead_block(name: str, failures: pd.DataFrame, detected: pd.Series, lead: pd.Series) -> dict:
    n_fail = len(failures)
    pos_win = _positive_window_mask(failures)
    n_win = int(pos_win.sum())
    n_det = int(detected.sum())
    summary = summarize_lead(name, lead, n_fail, n_det)
    summary["n_positive_window"] = n_win
    summary["pct_anticipated_all_failures"] = summary["pct_detected"]
    summary["pct_anticipated_given_window"] = (
        (100.0 * n_det / n_win) if n_win else 0.0
    )
    return summary


def _plot_pr(y_test, hgb_s, xgb_s, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.metrics import average_precision_score, precision_recall_curve

    fig, ax = plt.subplots(figsize=(8, 5.5))
    prev = float(np.mean(y_test))
    ax.axhline(prev, color="#888888", ls="--", lw=1, label=f"prevalence={prev:.4f}")
    for scores, label, color in (
        (hgb_s, "HistGB", "#1f4e79"),
        (xgb_s, "XGBoost", "#c45911"),
    ):
        p, r, _ = precision_recall_curve(y_test, scores)
        ap = float(average_precision_score(y_test, scores))
        ax.plot(r, p, color=color, lw=2, label=f"{label} (AP={ap:.3f})")
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_title("Primary test — Precision-Recall (HistGB vs XGBoost)")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_threshold(y_test, xgb_s, marks: list[tuple[float, str]], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    grid = np.linspace(0.05, 0.95, 37)
    rows = [operational_metrics(y_test, xgb_s, float(t)) for t in grid]
    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.plot(grid, [r["precision"] for r in rows], color="#1f4e79", lw=2, label="Precision")
    ax.plot(grid, [r["recall"] for r in rows], color="#c45911", lw=2, label="Recall")
    for t, lab in marks:
        ax.axvline(t, ls="--", lw=1, label=f"{lab} t={t:.3f}")
    ax.set_xlabel("Threshold")
    ax.set_ylabel("Score")
    ax.set_title("XGBoost test — threshold vs precision / recall")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_lead(leads: dict[str, np.ndarray], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"xgboost": "#c45911", "hist_gb": "#1f4e79", "reactive": "#6a994e"}
    fig, ax = plt.subplots(figsize=(8, 5.2))
    bins = [0, 1, 5, 15, 30, 60, 120, 240]
    for name, arr in leads.items():
        m = np.asarray(arr, dtype=float)
        m = m[np.isfinite(m)] / 60.0
        ax.hist(m, bins=bins, alpha=0.45, label=name, color=colors.get(name, "gray"))
    ax.set_xlabel("Lead time (minutes)")
    ax.set_ylabel("Failures detected before event_end")
    ax.set_title("Primary test — lead time comparison")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run_xgboost_candidate() -> dict:
    _require_xgb()
    prepared = prepare_primary_training()
    if prepared.t_train is None:
        raise ValueError("PreparedSplit.t_train is required for temporal validation")
    inner_mask, val_timestamp = temporal_val_mask(
        prepared.t_train, prepared.split_timestamp, TRAIN_TIME_FRAC
    )
    (
        x_inner,
        x_val,
        x_test,
        y_inner,
        y_val,
        y_test,
        names,
        prep_meta,
    ) = matrices_for_slices(
        prepared.X_train,
        prepared.y_train,
        inner_mask,
        prepared.X_test,
        prepared.y_test,
    )
    n_cat = int(prep_meta["task_type"]["n_train_categories"])
    spw = float(prep_meta["scale_pos_weight"])
    model, params = fit_xgboost(
        x_inner,
        y_inner,
        x_val,
        y_val,
        feature_names=names,
        n_categories=n_cat,
        scale_pos_weight=spw,
    )
    proba_val = _predict_proba(model, x_val, names, n_cat)
    proba_test = _predict_proba(model, x_test, names, n_cat)
    save_test_scores(y_test, proba_test, XGB_SCORES)

    chosen = select_thresholds_on_val(y_val, proba_val)
    op_t = float(chosen["operational"]["threshold"])

    hgb_y, hgb_s = load_test_scores(HGB_SCORES)
    if not np.array_equal(hgb_y, y_test):
        raise ValueError("HistGB saved y_test does not match current test labels")

    ranking = ranking_metrics(y_test, proba_test)
    xgb_05 = {**ranking, **operational_metrics(y_test, proba_test, 0.5)}
    xgb_op = {**ranking, **operational_metrics(y_test, proba_test, op_t)}
    hgb_rank = ranking_metrics(y_test, hgb_s)
    hgb_05 = {**hgb_rank, **operational_metrics(y_test, hgb_s, 0.5)}
    hgb_op = {**hgb_rank, **operational_metrics(y_test, hgb_s, HGB_WORKING_THRESHOLD)}

    frozen_test = {}
    for key, point in chosen.items():
        if key in ("split", "note", "operational", "validation_at_0.5"):
            continue
        if point is None:
            frozen_test[key] = None
            continue
        t = float(point["threshold"])
        frozen_test[key] = {
            "threshold_from_validation": t,
            "test": operational_metrics(y_test, proba_test, t),
        }
    frozen_test["operational"] = {
        "threshold_from_validation": op_t,
        "test": operational_metrics(y_test, proba_test, op_t),
    }
    frozen_test["threshold_0.5"] = {"threshold_from_validation": 0.5, "test": xgb_05}

    # Lead time on the same failures
    train_events, test_events, split = load_split_events()
    test_xgb = attach_scores(test_events, XGB_SCORES)
    test_hgb = attach_scores(test_events, HGB_SCORES)
    failures = test_xgb.loc[test_xgb["failed"] == 1].copy()
    hgb_fail = test_hgb.loc[test_hgb["failed"] == 1]
    xgb_p = failures["proba"].to_numpy()
    hgb_p = hgb_fail["proba"].to_numpy()
    xgb_det, xgb_lead = _detect(failures, xgb_p, op_t)
    hgb_det, hgb_lead = _detect(failures, hgb_p, HGB_WORKING_THRESHOLD)
    medians = train_runtime_medians(train_events)
    base_fire = reactive_fire_time(failures, medians)
    base_det = alert_before_failure(base_fire, failures["event_end"])
    base_lead = lead_seconds(base_fire, failures["event_end"])

    both_models = xgb_det & hgb_det
    delta_models = (
        pd.to_numeric(xgb_lead, errors="coerce")
        - pd.to_numeric(hgb_lead, errors="coerce")
    ).where(both_models, pd.NA)

    lead_report = {
        "xgb_threshold": op_t,
        "hgb_threshold": HGB_WORKING_THRESHOLD,
        "split_timestamp": int(split["split_timestamp"]),
        "definitions": {
            "alert_time": "decision_time if score >= threshold",
            "failure_time": "event_end",
            "lead_time": "event_end - alert_time only if alert_time < event_end",
        },
        "xgboost": lead_block("xgboost", failures, xgb_det, xgb_lead),
        "hist_gb": lead_block("hist_gb", failures, hgb_det, hgb_lead),
        "reactive": lead_block("reactive", failures, base_det, base_lead),
        "overlap": {
            "n_xgb_and_hgb": int(both_models.sum()),
            "n_xgb_not_hgb": int((xgb_det & ~hgb_det).sum()),
            "n_hgb_not_xgb": int((hgb_det & ~xgb_det).sum()),
            "n_xgb_not_reactive": int((xgb_det & ~base_det).sum()),
            "n_hgb_not_reactive": int((hgb_det & ~base_det).sum()),
            "delta_xgb_minus_hgb_seconds": _duration_from_series(delta_models),
        },
    }

    fig_pr = FIGURES_DIR / "primary_xgb_pr_curve.png"
    fig_thr = FIGURES_DIR / "primary_xgb_threshold_precision_recall.png"
    fig_lead = FIGURES_DIR / "primary_xgb_lead_time.png"
    _plot_pr(y_test, hgb_s, proba_test, fig_pr)
    _plot_threshold(
        y_test,
        proba_test,
        [(0.5, "0.5"), (op_t, "operational/val")],
        fig_thr,
    )
    _plot_lead(
        {
            "xgboost": pd.to_numeric(xgb_lead, errors="coerce").to_numpy(),
            "hist_gb": pd.to_numeric(hgb_lead, errors="coerce").to_numpy(),
            "reactive": pd.to_numeric(base_lead, errors="coerce").to_numpy(),
        },
        fig_lead,
    )

    baseline_report = {
        "split_timestamp": prepared.split_timestamp,
        "val_timestamp": val_timestamp,
        "val_rule": (
            f"inner train: start_time < {val_timestamp}; "
            f"validation: {val_timestamp} <= start_time < {prepared.split_timestamp}; "
            f"test: start_time >= {prepared.split_timestamp} (frozen)"
        ),
        "train_time_frac_inner": TRAIN_TIME_FRAC,
        "features": names,
        "preprocessing": prep_meta,
        "xgb_params": params,
        "scale_pos_weight": spw,
        "n_inner_train": int(len(y_inner)),
        "n_val": int(len(y_val)),
        "n_test": int(len(y_test)),
        "test_ranking": ranking,
        "test_threshold_0.5": xgb_05,
        "test_operational": xgb_op,
        "hist_gb_reference": {
            "note": (
                "Existing HistGB test scores (full original train, not inner-train). "
                f"Working threshold {HGB_WORKING_THRESHOLD} was frozen in the prior "
                "threshold study; 0.5 is the common un-tuned cut."
            ),
            "threshold_0.5": hgb_05,
            "threshold_operational": hgb_op,
        },
        "comparison_at_0.5": comparison_table(_row(hgb_05), _row(xgb_05)),
        "comparison_operational": comparison_table(_row(hgb_op), _row(xgb_op)),
        "figures": {
            "pr_curve": str(fig_pr.as_posix()),
            "threshold_vs_pr": str(fig_thr.as_posix()),
            "lead_time": str(fig_lead.as_posix()),
        },
        "scores_path": str(XGB_SCORES.as_posix()),
    }
    thresholds_report = {
        "val_timestamp": val_timestamp,
        "chosen_on": "temporal validation only",
        "validation": chosen,
        "test_at_frozen_thresholds": frozen_test,
    }
    lead_report["figure"] = str(fig_lead.as_posix())
    return {
        "baseline": baseline_report,
        "thresholds": thresholds_report,
        "lead_time": lead_report,
    }


def _duration_from_series(s: pd.Series) -> dict:
    from fpce.model.lead_time import _duration_stats

    return _duration_stats(pd.to_numeric(s, errors="coerce").dropna().to_numpy())


def main() -> None:
    parser = argparse.ArgumentParser(description="Train XGBoost candidate; do not overwrite HistGB reports.")
    args = parser.parse_args()
    del args
    bundle = run_xgboost_candidate()
    for path, key in (
        (XGB_BASELINE_JSON, "baseline"),
        (XGB_THRESHOLDS_JSON, "thresholds"),
        (XGB_LEAD_JSON, "lead_time"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bundle[key], indent=2), encoding="utf-8")
        print(f"[ok] wrote {path}")
    print(json.dumps(bundle["baseline"]["comparison_operational"], indent=2))
    print(json.dumps(bundle["lead_time"]["overlap"], indent=2))


if __name__ == "__main__":
    main()
