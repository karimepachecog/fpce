"""Primary-rack first ML baseline (always-0 + HistGradientBoosting).

Does not change the frozen time split or ``params/feature_contract.json``.
Lead-time and costing are out of scope.
"""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OrdinalEncoder

from fpce.config import REPORTS_DIR
from fpce.features.assemble import PreparedSplit, prepare_primary_training
from fpce.model.evaluate import ranking_metrics, select_f1_threshold, threshold_metrics

# Dropped at *model* time only. Still allowed in the feature contract.
EXCLUDE_NULL_HOST = ("mem_gps", "mkpi")
EXCLUDE_RAW_WHEN_FRAC = ("plan_cpu", "plan_mem")
EXCLUDE_HOST_ID = ("machine_id",)

CATEGORICAL = ("task_type",)

HGB_PARAMS = {
    "max_depth": 6,
    "learning_rate": 0.1,
    "max_iter": 100,
    "min_samples_leaf": 20,
    "l2_regularization": 0.0,
    "random_state": 0,
    "early_stopping": False,
}


def baseline_feature_columns(columns: list[str]) -> list[str]:
    drop = set(EXCLUDE_NULL_HOST) | set(EXCLUDE_RAW_WHEN_FRAC) | set(EXCLUDE_HOST_ID)
    return [c for c in columns if c not in drop]


def _balanced_sample_weight(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, dtype=int)
    n = len(y)
    n_pos = max(int((y == 1).sum()), 1)
    n_neg = max(int((y == 0).sum()), 1)
    w = np.empty(n, dtype=float)
    w[y == 1] = n / (2.0 * n_pos)
    w[y == 0] = n / (2.0 * n_neg)
    return w


def _hgb_supports_class_weight() -> bool:
    return "class_weight" in inspect.signature(
        HistGradientBoostingClassifier.__init__
    ).parameters


def encode_task_type(
    train: pd.Series,
    test: pd.Series,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Ordinal codes from train categories; unseen test levels → NaN (missing)."""
    encoder, x_train, meta = fit_task_type_encoder(train)
    x_test = apply_task_type_encoder(encoder, test)
    return x_train, x_test, meta


def fit_task_type_encoder(train: pd.Series) -> tuple[OrdinalEncoder, np.ndarray, dict]:
    encoder = OrdinalEncoder(
        handle_unknown="use_encoded_value",
        unknown_value=np.nan,
        encoded_missing_value=np.nan,
        dtype=np.float64,
    )
    x_train = encoder.fit_transform(train.to_frame()).ravel()
    categories = [str(v) for v in encoder.categories_[0].tolist()]
    meta = {
        "encoder": "sklearn.preprocessing.OrdinalEncoder",
        "handle_unknown": "use_encoded_value",
        "unknown_value": "NaN",
        "encoded_missing_value": "NaN",
        "train_categories": categories,
        "n_train_categories": len(categories),
        "note": (
            "Unseen levels become NaN (missing). Encoder is fit on the training "
            "slice only — never on validation or test."
        ),
    }
    return encoder, x_train, meta


def apply_task_type_encoder(encoder: OrdinalEncoder, series: pd.Series) -> np.ndarray:
    return encoder.transform(series.to_frame()).ravel()


def fit_design_transformers(
    prepared: PreparedSplit,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    list[str],
    dict,
    SimpleImputer,
    OrdinalEncoder,
]:
    """Impute/encode on train only. Returns matrices plus fitted transformers."""
    features = baseline_feature_columns(prepared.feature_columns)
    if "task_type" not in features:
        raise ValueError("task_type is required as a categorical feature")
    numeric = [c for c in features if c not in CATEGORICAL]
    X_train = prepared.X_train[features]
    X_test = prepared.X_test[features]

    imputer = SimpleImputer(strategy="median")
    train_num = imputer.fit_transform(X_train[numeric])
    test_num = imputer.transform(X_test[numeric])
    encoder, cat_train, cat_meta = fit_task_type_encoder(X_train["task_type"])
    cat_test = apply_task_type_encoder(encoder, X_test["task_type"])

    # Categorical column first so HistGB categorical_features=[0].
    x_train = np.column_stack([cat_train, train_num])
    x_test = np.column_stack([cat_test, test_num])
    ordered = ["task_type", *numeric]
    prep_meta = {
        "feature_order": ordered,
        "numeric_features": numeric,
        "categorical_features": ["task_type"],
        "numeric_imputer": {
            "strategy": "median",
            "statistics_from": "X_train only",
            "medians": {
                col: (None if np.isnan(val) else float(val))
                for col, val in zip(numeric, imputer.statistics_)
            },
        },
        "task_type": cat_meta,
        "dropped": sorted(
            set(prepared.feature_columns) - set(features)
        ),
    }
    y_train = prepared.y_train.to_numpy(dtype=int)
    y_test = prepared.y_test.to_numpy(dtype=int)
    return x_train, x_test, y_train, y_test, ordered, prep_meta, imputer, encoder


def prepare_design_matrices(
    prepared: PreparedSplit,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str], dict]:
    x_train, x_test, y_train, y_test, ordered, prep_meta, _, _ = fit_design_transformers(
        prepared
    )
    return x_train, x_test, y_train, y_test, ordered, prep_meta


def temporal_val_mask(t_train: pd.Series, split_timestamp: int, train_time_frac: float = 0.8) -> tuple[np.ndarray, int]:
    """Last slice of *train time* is validation. No shuffle. Test is untouched."""
    t = pd.to_numeric(t_train, errors="coerce")
    t_min = int(t.min())
    val_timestamp = int(t_min + train_time_frac * (int(split_timestamp) - t_min))
    inner = (t < val_timestamp).to_numpy()
    if inner.all() or not inner.any():
        raise ValueError(
            f"temporal validation split is empty or full (val_timestamp={val_timestamp})"
        )
    return inner, val_timestamp


def matrices_for_slices(
    X: pd.DataFrame,
    y: pd.Series,
    inner_mask: np.ndarray,
    X_test: pd.DataFrame,
    y_test: pd.Series,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str], dict]:
    """Impute/encode on inner train only; transform val and test."""
    features = baseline_feature_columns(X.columns.tolist())
    if "task_type" not in features:
        raise ValueError("task_type is required as a categorical feature")
    numeric = [c for c in features if c not in CATEGORICAL]
    X_inner = X.loc[inner_mask, features]
    X_val = X.loc[~inner_mask, features]
    y_inner = y.loc[inner_mask].to_numpy(dtype=int)
    y_val = y.loc[~inner_mask].to_numpy(dtype=int)

    imputer = SimpleImputer(strategy="median")
    inner_num = imputer.fit_transform(X_inner[numeric])
    val_num = imputer.transform(X_val[numeric])
    test_num = imputer.transform(X_test[features][numeric])
    encoder, inner_cat, cat_meta = fit_task_type_encoder(X_inner["task_type"])
    val_cat = apply_task_type_encoder(encoder, X_val["task_type"])
    test_cat = apply_task_type_encoder(encoder, X_test[features]["task_type"])

    x_inner = np.column_stack([inner_cat, inner_num])
    x_val = np.column_stack([val_cat, val_num])
    x_test = np.column_stack([test_cat, test_num])
    ordered = ["task_type", *numeric]
    n_pos = int((y_inner == 1).sum())
    n_neg = int((y_inner == 0).sum())
    prep_meta = {
        "feature_order": ordered,
        "numeric_features": numeric,
        "categorical_features": ["task_type"],
        "task_type_representation": (
            "Ordinal codes from inner-train categories; unknown/NaN stay missing. "
            "Passed to XGBoost as pandas 'category' with enable_categorical=True "
            "(same codes HistGB uses via categorical_features=[0]). No numeric scaling."
        ),
        "numeric_imputer": {
            "strategy": "median",
            "statistics_from": "inner train only (validation and test unused)",
            "medians": {
                col: (None if np.isnan(val) else float(val))
                for col, val in zip(numeric, imputer.statistics_)
            },
        },
        "task_type": cat_meta,
        "scale_pos_weight": (n_neg / n_pos) if n_pos else None,
        "n_inner_train": int(len(y_inner)),
        "n_inner_positive": n_pos,
        "n_inner_negative": n_neg,
        "n_val": int(len(y_val)),
        "n_test": int(len(y_test)),
    }
    return (
        x_inner,
        x_val,
        x_test,
        y_inner,
        y_val,
        y_test.to_numpy(dtype=int),
        ordered,
        prep_meta,
    )


def fit_hist_gb(
    x_train: np.ndarray,
    y_train: np.ndarray,
) -> tuple[HistGradientBoostingClassifier, dict]:
    params = dict(HGB_PARAMS)
    weight_meta: dict
    fit_kwargs: dict = {}
    if _hgb_supports_class_weight():
        params["class_weight"] = "balanced"
        weight_meta = {
            "method": "class_weight='balanced'",
            "sample_weight": None,
        }
    else:
        fit_kwargs["sample_weight"] = _balanced_sample_weight(y_train)
        weight_meta = {
            "method": "sample_weight n/(2 n_c) from y_train only",
            "sample_weight": "balanced_from_y_train",
        }
    params["categorical_features"] = [0]
    model = HistGradientBoostingClassifier(**params)
    model.fit(x_train, y_train, **fit_kwargs)
    used = {k: params[k] for k in params}
    used["sklearn_estimator"] = "HistGradientBoostingClassifier"
    used["imbalance"] = weight_meta
    return model, used


def always_zero_scores(n: int) -> np.ndarray:
    return np.zeros(n, dtype=float)


def evaluate_scores(name: str, y_true: np.ndarray, scores: np.ndarray, threshold: float) -> dict:
    return {
        "name": name,
        **ranking_metrics(y_true, scores),
        **threshold_metrics(y_true, scores, threshold),
    }


def run_primary_baseline(prepared: PreparedSplit | None = None) -> dict:
    if prepared is None:
        prepared = prepare_primary_training()
    x_train, x_test, y_train, y_test, ordered, prep_meta = prepare_design_matrices(
        prepared
    )

    trivial_test = evaluate_scores(
        "always_predict_0",
        y_test,
        always_zero_scores(len(y_test)),
        threshold=0.5,
    )

    model, hgb_params = fit_hist_gb(x_train, y_train)
    train_proba = model.predict_proba(x_train)[:, 1]
    test_proba = model.predict_proba(x_test)[:, 1]
    chosen = select_f1_threshold(y_train, train_proba)

    hgb_05 = evaluate_scores("hist_gb_threshold_0.5", y_test, test_proba, 0.5)
    hgb_train_t = evaluate_scores(
        "hist_gb_threshold_train_f1",
        y_test,
        test_proba,
        chosen["threshold"],
    )

    def lift_vs_trivial(hgb: dict) -> dict:
        return {
            "delta_true_positives": hgb["true_positives"] - trivial_test["true_positives"],
            "delta_false_positives": hgb["false_positives"] - trivial_test["false_positives"],
            "delta_f1": hgb["f1"] - trivial_test["f1"],
            "delta_recall": hgb["recall"] - trivial_test["recall"],
            "delta_precision": hgb["precision"] - trivial_test["precision"],
            "pr_auc_vs_prevalence": hgb["pr_auc"] - trivial_test["positive_rate"],
            "note": (
                "Always-0 has F1=0 and recall=0. PR-AUC of a constant-0 score "
                "equals test prevalence; ROC-AUC is 0.5. Ranking metrics do not "
                "use a threshold."
            ),
        }

    report = {
        "split_timestamp": prepared.split_timestamp,
        "split_column": prepared.split_column,
        "target": prepared.target,
        "n_train": int(len(y_train)),
        "n_test": int(len(y_test)),
        "features": ordered,
        "preprocessing": prep_meta,
        "hist_gb_params": hgb_params,
        "train_chosen_threshold": chosen,
        "always_predict_0": trivial_test,
        "hist_gb": {
            "threshold_0.5": hgb_05,
            "threshold_train_f1": hgb_train_t,
        },
        "improvement_vs_always_0": {
            "threshold_0.5": lift_vs_trivial(hgb_05),
            "threshold_train_f1": lift_vs_trivial(hgb_train_t),
        },
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train always-0 + HistGB primary baseline; do not tune."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPORTS_DIR / "primary_hgb_baseline.json",
    )
    args = parser.parse_args()
    report = run_primary_baseline()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"[ok] wrote {args.output}")


if __name__ == "__main__":
    main()
