"""XGB v2: few slower/regularized configs. Does not overwrite XGB v1 reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fpce.config import REPORTS_DIR
from fpce.features.assemble import prepare_primary_training
from fpce.model.baseline import reactive_fire_time, train_runtime_medians
from fpce.model.evaluate import operational_metrics, ranking_metrics
from fpce.model.lead_time import (
    FIGURES_DIR,
    alert_before_failure,
    attach_scores,
    lead_seconds,
    load_split_events,
)
from fpce.model.threshold_analysis import load_test_scores, save_test_scores
from fpce.model.train import matrices_for_slices, temporal_val_mask
from fpce.model.xgboost_train import (
    HGB_SCORES,
    HGB_WORKING_THRESHOLD,
    TRAIN_TIME_FRAC,
    XGB_SCORES as XGB_V1_SCORES,
    _detect,
    _duration_from_series,
    _plot_lead,
    _plot_pr,
    _plot_threshold,
    _predict_proba,
    _require_xgb,
    as_xgb_frame,
    lead_block,
    select_thresholds_on_val,
)

try:
    from xgboost import XGBClassifier
except ImportError:  # pragma: no cover
    XGBClassifier = None  # type: ignore[misc, assignment]

SHARED = {
    "objective": "binary:logistic",
    "eval_metric": "aucpr",
    "tree_method": "hist",
    "enable_categorical": True,
    "random_state": 0,
    "n_jobs": -1,
    "early_stopping_rounds": 50,
}

CONFIGS: dict[str, dict] = {
    "A": {
        "learning_rate": 0.02,
        "max_depth": 4,
        "min_child_weight": 50,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_lambda": 5.0,
        "reg_alpha": 0.0,
        "n_estimators": 500,
    },
    "B": {
        "learning_rate": 0.03,
        "max_depth": 5,
        "min_child_weight": 50,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_lambda": 10.0,
        "reg_alpha": 0.1,
        "n_estimators": 500,
    },
    "C": {
        "learning_rate": 0.05,
        "max_depth": 4,
        "min_child_weight": 100,
        "subsample": 0.7,
        "colsample_bytree": 0.7,
        "reg_lambda": 10.0,
        "reg_alpha": 0.1,
        "n_estimators": 300,
    },
}

VAL_JSON = REPORTS_DIR / "primary_xgb_v2_validation.json"
BASE_JSON = REPORTS_DIR / "primary_xgb_v2_baseline.json"
THR_JSON = REPORTS_DIR / "primary_xgb_v2_thresholds.json"
LEAD_JSON = REPORTS_DIR / "primary_xgb_v2_lead_time.json"
SCORES_NPZ = REPORTS_DIR / "primary_xgb_v2_test_scores.npz"
V1_GUARD = (
    REPORTS_DIR / "primary_xgb_baseline.json",
    REPORTS_DIR / "primary_xgb_thresholds.json",
    REPORTS_DIR / "primary_xgb_lead_time.json",
    REPORTS_DIR / "primary_xgb_test_scores.npz",
)


def _iteration_info(model) -> dict:
    booster = model.get_booster()
    best_it = getattr(model, "best_iteration", None)
    if best_it is None:
        best_it = getattr(booster, "best_iteration", None)
    n_rounds = int(booster.num_boosted_rounds())
    return {
        "best_iteration": None if best_it is None else int(best_it),
        "num_boosted_rounds": n_rounds,
        "best_score": (
            None
            if getattr(model, "best_score", None) is None
            else float(model.best_score)
        ),
    }


def fit_config(
    name: str,
    extra: dict,
    *,
    x_inner,
    y_inner,
    x_val,
    y_val,
    names: list[str],
    n_categories: int,
    scale_pos_weight: float,
) -> tuple[object, dict, np.ndarray]:
    _require_xgb()
    params = {**SHARED, **extra, "scale_pos_weight": float(scale_pos_weight)}
    model = XGBClassifier(**params)
    X_tr = as_xgb_frame(x_inner, names, n_categories)
    X_va = as_xgb_frame(x_val, names, n_categories)
    model.fit(X_tr, y_inner, eval_set=[(X_va, y_val)], verbose=False)
    proba_val = _predict_proba(model, x_val, names, n_categories)
    rank = ranking_metrics(y_val, proba_val)
    at_05 = operational_metrics(y_val, proba_val, 0.5)
    info = _iteration_info(model)
    report = {
        "name": name,
        "params": params,
        **info,
        "validation": {
            "pr_auc": rank["pr_auc"],
            "roc_auc": rank["roc_auc"],
            "threshold_0.5": at_05,
        },
    }
    return model, report, proba_val


def pick_winner(rows: list[dict]) -> dict:
    """Validation-only: PR-AUC, then fewer FP at 0.5, then higher recall at 0.5."""

    def key(r: dict) -> tuple:
        v = r["validation"]
        t = v["threshold_0.5"]
        fp = t["false_positives"]
        rec = t["recall"]
        return (float(v["pr_auc"]), -float(fp), float(rec))

    winner = max(rows, key=key)
    return {
        "winner": winner["name"],
        "reason": (
            "Highest validation PR-AUC, then lowest FP at threshold 0.5, "
            "then highest recall at 0.5. Test unused."
        ),
        "ranking": [r["name"] for r in sorted(rows, key=key, reverse=True)],
    }


def all_stalled(rows: list[dict], near: int = 2) -> bool:
    return all(
        r["best_iteration"] is not None and int(r["best_iteration"]) <= near
        for r in rows
    )


def run_v2() -> dict:
    for path in V1_GUARD:
        if not path.exists():
            raise FileNotFoundError(f"XGB v1 artifact missing: {path}")
    prepared = prepare_primary_training()
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

    fitted: dict[str, object] = {}
    val_rows: list[dict] = []
    val_probas: dict[str, np.ndarray] = {}
    for name, extra in CONFIGS.items():
        model, report, proba_val = fit_config(
            name,
            extra,
            x_inner=x_inner,
            y_inner=y_inner,
            x_val=x_val,
            y_val=y_val,
            names=names,
            n_categories=n_cat,
            scale_pos_weight=spw,
        )
        fitted[name] = model
        val_rows.append(report)
        val_probas[name] = proba_val

    stalled = all_stalled(val_rows)
    choice = pick_winner(val_rows)
    winner_name = choice["winner"]
    winner_model = fitted[winner_name]
    proba_val = val_probas[winner_name]
    chosen = select_thresholds_on_val(y_val, proba_val)
    op_t = float(chosen["operational"]["threshold"])

    # Test is scored once, after config + threshold are frozen.
    proba_test = _predict_proba(winner_model, x_test, names, n_cat)
    save_test_scores(y_test, proba_test, SCORES_NPZ)

    hgb_y, hgb_s = load_test_scores(HGB_SCORES)
    v1_y, v1_s = load_test_scores(XGB_V1_SCORES)
    if not np.array_equal(hgb_y, y_test) or not np.array_equal(v1_y, y_test):
        raise ValueError("saved score labels do not match current test y")

    ranking = ranking_metrics(y_test, proba_test)
    xgb2_op = {**ranking, **operational_metrics(y_test, proba_test, op_t)}
    xgb2_05 = {**ranking, **operational_metrics(y_test, proba_test, 0.5)}
    hgb_rank = ranking_metrics(y_test, hgb_s)
    hgb_op = {**hgb_rank, **operational_metrics(y_test, hgb_s, HGB_WORKING_THRESHOLD)}
    v1_rank = ranking_metrics(y_test, v1_s)
    # XGB v1 frozen operational from val precision>=0.10
    v1_op_t = 0.5096072554588318
    v1_op = {**v1_rank, **operational_metrics(y_test, v1_s, v1_op_t)}

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

    train_events, test_events, split = load_split_events()
    test_v2 = attach_scores(test_events, SCORES_NPZ)
    test_v1 = attach_scores(test_events, XGB_V1_SCORES)
    test_hgb = attach_scores(test_events, HGB_SCORES)
    failures = test_v2.loc[test_v2["failed"] == 1].copy()
    v2_det, v2_lead = _detect(failures, failures["proba"].to_numpy(), op_t)
    v1_det, v1_lead = _detect(
        failures, test_v1.loc[test_v1["failed"] == 1, "proba"].to_numpy(), v1_op_t
    )
    hgb_det, hgb_lead = _detect(
        failures,
        test_hgb.loc[test_hgb["failed"] == 1, "proba"].to_numpy(),
        HGB_WORKING_THRESHOLD,
    )
    medians = train_runtime_medians(train_events)
    base_fire = reactive_fire_time(failures, medians)
    base_det = alert_before_failure(base_fire, failures["event_end"])
    base_lead = lead_seconds(base_fire, failures["event_end"])

    fig_pr = FIGURES_DIR / "primary_xgb_v2_pr_curve.png"
    fig_thr = FIGURES_DIR / "primary_xgb_v2_threshold_precision_recall.png"
    fig_lead = FIGURES_DIR / "primary_xgb_v2_lead_time.png"
    _plot_pr(y_test, hgb_s, proba_test, fig_pr)
    _plot_threshold(y_test, proba_test, [(0.5, "0.5"), (op_t, "operational/val")], fig_thr)
    _plot_lead(
        {
            "xgboost": pd.to_numeric(v2_lead, errors="coerce").to_numpy(),
            "hist_gb": pd.to_numeric(hgb_lead, errors="coerce").to_numpy(),
            "reactive": pd.to_numeric(base_lead, errors="coerce").to_numpy(),
        },
        fig_lead,
    )

    validation_report = {
        "val_timestamp": val_timestamp,
        "split_timestamp": prepared.split_timestamp,
        "early_stopping_rounds": 50,
        "scale_pos_weight": spw,
        "stalled_all_best_iteration_near_zero": stalled,
        "configs": val_rows,
        "selection": choice,
        "fourth_config": (
            "Not run: A/B/C already test whether slower/regularized boosting "
            "uses more trees. Adding a fourth config would be extra tuning."
        ),
    }
    baseline_report = {
        "winner": winner_name,
        "val_timestamp": val_timestamp,
        "stalled_boosting": stalled,
        "features": names,
        "preprocessing": prep_meta,
        "winner_params": next(r["params"] for r in val_rows if r["name"] == winner_name),
        "winner_best_iteration": next(
            r["best_iteration"] for r in val_rows if r["name"] == winner_name
        ),
        "test_ranking": ranking,
        "test_threshold_0.5": xgb2_05,
        "test_operational": xgb2_op,
        "references_test": {
            "hist_gb_t0.9": hgb_op,
            "xgb_v1_operational": v1_op,
        },
        "figures": {
            "pr_curve": str(fig_pr.as_posix()),
            "threshold_vs_pr": str(fig_thr.as_posix()),
            "lead_time": str(fig_lead.as_posix()),
        },
        "scores_path": str(SCORES_NPZ.as_posix()),
        "v1_files_untouched": [str(p.as_posix()) for p in V1_GUARD],
    }
    thresholds_report = {
        "winner": winner_name,
        "chosen_on": "temporal validation only",
        "validation": chosen,
        "test_at_frozen_thresholds": frozen_test,
    }
    lead_report = {
        "xgb_v2_threshold": op_t,
        "xgb_v1_threshold": v1_op_t,
        "hgb_threshold": HGB_WORKING_THRESHOLD,
        "split_timestamp": int(split["split_timestamp"]),
        "xgboost_v2": lead_block("xgboost_v2", failures, v2_det, v2_lead),
        "xgboost_v1": lead_block("xgboost_v1", failures, v1_det, v1_lead),
        "hist_gb": lead_block("hist_gb", failures, hgb_det, hgb_lead),
        "reactive": lead_block("reactive", failures, base_det, base_lead),
        "overlap": {
            "n_v2_and_hgb": int((v2_det & hgb_det).sum()),
            "n_v2_not_hgb": int((v2_det & ~hgb_det).sum()),
            "n_hgb_not_v2": int((hgb_det & ~v2_det).sum()),
            "n_v2_not_v1": int((v2_det & ~v1_det).sum()),
            "n_v1_not_v2": int((v1_det & ~v2_det).sum()),
            "n_v2_not_reactive": int((v2_det & ~base_det).sum()),
            "delta_v2_minus_hgb_seconds": _duration_from_series(
                (
                    pd.to_numeric(v2_lead, errors="coerce")
                    - pd.to_numeric(hgb_lead, errors="coerce")
                ).where(v2_det & hgb_det, pd.NA)
            ),
        },
        "figure": str(fig_lead.as_posix()),
    }
    return {
        "validation": validation_report,
        "baseline": baseline_report,
        "thresholds": thresholds_report,
        "lead_time": lead_report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="XGB v2 configs; do not overwrite v1")
    parser.parse_args()
    v1_mtime = {p: p.stat().st_mtime for p in V1_GUARD}
    bundle = run_v2()
    for path, key in (
        (VAL_JSON, "validation"),
        (BASE_JSON, "baseline"),
        (THR_JSON, "thresholds"),
        (LEAD_JSON, "lead_time"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bundle[key], indent=2), encoding="utf-8")
        print(f"[ok] wrote {path}")
    for p, mtime in v1_mtime.items():
        if p.stat().st_mtime != mtime:
            raise RuntimeError(f"XGB v1 artifact was modified: {p}")
    print(json.dumps(bundle["validation"]["selection"], indent=2))
    print("stalled", bundle["validation"]["stalled_all_best_iteration_near_zero"])
    print(json.dumps(bundle["baseline"]["test_operational"], indent=2)[:800])


if __name__ == "__main__":
    main()
