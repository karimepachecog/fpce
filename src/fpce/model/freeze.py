"""Persist the frozen Role B HistGB bundle and the C/D handoff table.

Does not tune hyperparameters or overwrite HistGB / XGBoost evaluation reports.
The classifier is refit once with the documented ``HGB_PARAMS`` so the bundle
can be loaded later without training. Test probabilities are checked against
``reports/primary_hgb_test_scores.npz``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from fpce.config import MODELS_DIR, REPORTS_DIR, repo_relpath, resolve_repo_path
from fpce.contracts import load_feature_contract
from fpce.features.assemble import load_time_split, load_trainable_events
from fpce.model.baseline import reactive_fire_time, train_runtime_medians
from fpce.model.lead_time import WORKING_THRESHOLD, alert_before_failure, lead_seconds
from fpce.model.threshold_analysis import SCORES_PATH, load_test_scores
from fpce.model.train import HGB_PARAMS, fit_design_transformers, fit_hist_gb

HANDOFF_PATH = REPORTS_DIR / "role_b_handoff.parquet"
MANIFEST_PATH = REPORTS_DIR / "role_b_handoff_manifest.json"
BUNDLE_PATH = MODELS_DIR / "primary_hgb_frozen.joblib"
FREEZE_META_PATH = REPORTS_DIR / "role_b_frozen_model.json"

HANDOFF_EXTRA_COLUMNS = (
    "instance_name",
    "task_name",
    "job_name",
    "event_end",
    "end_time",
    "seq_no",
    "total_seq_no",
    "task_type",
    "waste_window_seconds",
    "waste_window_imputed",
    "eligible_for_costing",
)

ID_COLUMNS = (
    "test_row_index",
    "instance_name",
    "task_name",
    "job_name",
    "machine_id",
    "start_time",
    "seq_no",
    "total_seq_no",
    "task_type",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_series(frame: pd.DataFrame, name: str) -> pd.Series:
    if name in frame.columns:
        return frame[name]
    return pd.Series(pd.NA, index=frame.index, dtype="object")


def build_handoff_frame(
    test: pd.DataFrame,
    scores: np.ndarray,
    runtime_medians: pd.Series,
    *,
    threshold: float = WORKING_THRESHOLD,
) -> pd.DataFrame:
    """One row per frozen-test eligible event. Lead-time fields follow Role B defs."""
    if len(test) != len(scores):
        raise ValueError(
            f"test rows ({len(test)}) != scores ({len(scores)}); cannot align"
        )
    y = pd.to_numeric(test["failed"], errors="coerce").fillna(0).astype(int).to_numpy()
    out = pd.DataFrame(
        {
            "test_row_index": np.arange(len(test), dtype=np.int64),
            "instance_name": _optional_series(test, "instance_name"),
            "task_name": _optional_series(test, "task_name"),
            "job_name": _optional_series(test, "job_name"),
            "machine_id": test["machine_id"],
            "start_time": test["start_time"] if "start_time" in test.columns else pd.NA,
            "seq_no": _optional_series(test, "seq_no"),
            "total_seq_no": _optional_series(test, "total_seq_no"),
            "task_type": _optional_series(test, "task_type"),
            "decision_time": pd.to_numeric(test["decision_time"], errors="coerce"),
            "event_end": pd.to_numeric(test["event_end"], errors="coerce"),
            "end_time": (
                pd.to_numeric(test["end_time"], errors="coerce")
                if "end_time" in test.columns
                else pd.NA
            ),
            "failed": y,
            "waste_window_seconds": (
                pd.to_numeric(test["waste_window_seconds"], errors="coerce")
                if "waste_window_seconds" in test.columns
                else pd.NA
            ),
            "waste_window_imputed": _optional_series(test, "waste_window_imputed"),
            "eligible_for_costing": (
                pd.to_numeric(test["eligible_for_costing"], errors="coerce")
                .fillna(0)
                .astype(int)
                if "eligible_for_costing" in test.columns
                else 0
            ),
            "model_score": np.asarray(scores, dtype=np.float32),
        }
    )
    decision = out["decision_time"]
    end = out["event_end"]
    out["has_positive_measurable_window"] = (
        end.notna() & decision.notna() & (end > decision)
    ).astype(int)
    out["model_alert"] = (out["model_score"] >= float(threshold)).astype(int)
    out["model_alert_time"] = decision.where(out["model_alert"] == 1, pd.NA)
    out["model_lead_time_seconds"] = lead_seconds(out["model_alert_time"], end)
    out["baseline_alert_time"] = reactive_fire_time(test, runtime_medians)
    out["baseline_alert"] = alert_before_failure(out["baseline_alert_time"], end).astype(
        int
    )
    out["baseline_lead_time_seconds"] = lead_seconds(out["baseline_alert_time"], end)
    both = (out["model_alert"] == 1) & (out["baseline_alert"] == 1)
    model_lead = pd.to_numeric(out["model_lead_time_seconds"], errors="coerce")
    base_lead = pd.to_numeric(out["baseline_lead_time_seconds"], errors="coerce")
    out["delta_lead_time_seconds"] = (model_lead - base_lead).where(
        both & model_lead.notna() & base_lead.notna(), pd.NA
    )
    ordered = [
        *ID_COLUMNS,
        "decision_time",
        "event_end",
        "end_time",
        "failed",
        "has_positive_measurable_window",
        "waste_window_seconds",
        "waste_window_imputed",
        "eligible_for_costing",
        "model_score",
        "model_alert",
        "model_alert_time",
        "model_lead_time_seconds",
        "baseline_alert",
        "baseline_alert_time",
        "baseline_lead_time_seconds",
        "delta_lead_time_seconds",
    ]
    return out.loc[:, [c for c in ordered if c in out.columns]]


def write_handoff_parquet(frame: pd.DataFrame, path: Path = HANDOFF_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def handoff_summary(frame: pd.DataFrame, *, threshold: float) -> dict:
    failed = frame["failed"] == 1
    n_fail = int(failed.sum())
    window = failed & (frame["has_positive_measurable_window"] == 1)
    anticipated = failed & frame["model_lead_time_seconds"].notna()
    n_window = int(window.sum())
    n_anticipated = int(anticipated.sum())
    return {
        "n_rows": int(len(frame)),
        "n_failures": n_fail,
        "n_failures_with_positive_measurable_window": n_window,
        "n_failures_without_measurable_lead_time": int(n_fail - n_window),
        "model_threshold": float(threshold),
        "n_model_alerts": int((frame["model_alert"] == 1).sum()),
        "n_failures_anticipated_by_model": n_anticipated,
        "pct_of_windowed_failures_anticipated": (
            (100.0 * n_anticipated / n_window) if n_window else 0.0
        ),
        "n_failures_anticipated_by_reactive_baseline": int(
            (failed & (frame["baseline_alert"] == 1)).sum()
        ),
        "n_eligible_for_costing": int((frame["eligible_for_costing"] == 1).sum()),
        "n_costing_eligible_failures": int(
            (failed & (frame["eligible_for_costing"] == 1)).sum()
        ),
        "join_keys_to_instance_events": [
            "instance_name",
            "machine_id",
            "start_time",
            "seq_no",
            "decision_time",
            "test_row_index",
        ],
        "note": (
            "Lead-time fields are null unless alert_time < event_end. "
            "event_end <= decision_time yields no measurable lead. "
            "Role C must not compute kWh/liters from this file yet; filter "
            "eligible_for_costing=1 (and typically failed=1). "
            "XGBoost is not the official Role B model."
        ),
    }


def persist_frozen_model(
    *,
    scores_path: Path = SCORES_PATH,
    bundle_path: Path = BUNDLE_PATH,
    max_abs_score_diff: float = 1e-5,
) -> dict:
    """Refit the documented HistGB and dump model + train-only transformers."""
    from fpce.features.assemble import prepare_primary_training

    y_cached, proba_cached = load_test_scores(scores_path)
    prepared = prepare_primary_training()
    (
        x_train,
        x_test,
        y_train,
        y_test,
        ordered,
        prep_meta,
        imputer,
        encoder,
    ) = fit_design_transformers(prepared)
    if not np.array_equal(y_test, y_cached):
        raise ValueError("frozen y_test does not match reports/primary_hgb_test_scores.npz")
    model, hgb_params = fit_hist_gb(x_train, y_train)
    proba = model.predict_proba(x_test)[:, 1]
    abs_diff = np.abs(proba.astype(np.float64) - proba_cached.astype(np.float64))
    max_diff = float(abs_diff.max()) if len(abs_diff) else 0.0
    if max_diff > max_abs_score_diff:
        raise ValueError(
            f"refit test scores differ from cached npz (max abs {max_diff}); "
            "refusing to freeze a mismatched model"
        )
    bundle = {
        "sklearn_estimator": "HistGradientBoostingClassifier",
        "role": "B",
        "status": "frozen_official",
        "threshold": float(WORKING_THRESHOLD),
        "target": "failed",
        "split_timestamp": int(prepared.split_timestamp),
        "split_column": prepared.split_column,
        "feature_order": ordered,
        "preprocessing": prep_meta,
        "hgb_params": hgb_params,
        "imputer": imputer,
        "task_type_encoder": encoder,
        "model": model,
        "sklearn_version": sklearn.__version__,
        "scores_path": str(scores_path.as_posix()),
        "score_check": {
            "max_abs_diff_vs_npz": max_diff,
            "tolerance": max_abs_score_diff,
            "n_test": int(len(y_test)),
        },
    }
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, bundle_path, compress=3)
    return {
        "bundle_path": str(bundle_path.as_posix()),
        "sklearn_version": sklearn.__version__,
        "n_train": int(len(y_train)),
        "n_test": int(len(y_test)),
        "feature_order": ordered,
        "threshold": float(WORKING_THRESHOLD),
        "hgb_params": {k: v for k, v in hgb_params.items() if k != "imbalance"}
        | {"imbalance": hgb_params.get("imbalance")},
        "score_check": bundle["score_check"],
        "hgb_params_frozen": dict(HGB_PARAMS),
    }


def transform_with_bundle(bundle: dict, features: pd.DataFrame) -> np.ndarray:
    """Apply the frozen imputer/encoder. ``features`` must include feature_order cols."""
    ordered = list(bundle["feature_order"])
    numeric = [c for c in ordered if c != "task_type"]
    missing = [c for c in ordered if c not in features.columns]
    if missing:
        raise ValueError(f"missing features for frozen model: {missing}")
    cat = bundle["task_type_encoder"].transform(features[["task_type"]]).ravel()
    num = bundle["imputer"].transform(features[numeric])
    return np.column_stack([cat, num])


def predict_proba_with_bundle(bundle: dict, features: pd.DataFrame) -> np.ndarray:
    return bundle["model"].predict_proba(transform_with_bundle(bundle, features))[:, 1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Write Role B handoff parquet and persist the frozen HistGB bundle. "
            "Does not tune or overwrite evaluation JSON."
        )
    )
    parser.add_argument("--handoff", type=Path, default=HANDOFF_PATH)
    parser.add_argument("--bundle", type=Path, default=BUNDLE_PATH)
    parser.add_argument("--scores", type=Path, default=SCORES_PATH)
    parser.add_argument("--threshold", type=float, default=WORKING_THRESHOLD)
    parser.add_argument(
        "--skip-model",
        action="store_true",
        help="Write the handoff table only (uses cached test scores).",
    )
    parser.add_argument(
        "--skip-handoff",
        action="store_true",
        help="Persist the model bundle only.",
    )
    args = parser.parse_args()

    manifest: dict = {
        "official_model": "HistGradientBoostingClassifier",
        "official_threshold": float(args.threshold),
        "xgboost_status": (
            "Documented experiments only; not the official Role B model."
        ),
    }

    if not args.skip_handoff:
        contract = load_feature_contract()
        split = load_time_split()
        events_path = resolve_repo_path(split["instance_events_path"])
        events = load_trainable_events(
            events_path, contract, extra_columns=HANDOFF_EXTRA_COLUMNS
        )
        split_ts = int(split["split_timestamp"])
        train = events.loc[events[contract.split_column] < split_ts].reset_index(
            drop=True
        )
        test = events.loc[events[contract.split_column] >= split_ts].reset_index(
            drop=True
        )
        y_npz, proba = load_test_scores(args.scores)
        y_test = pd.to_numeric(test["failed"], errors="coerce").fillna(0).astype(int).to_numpy()
        if not np.array_equal(y_test, y_npz):
            raise ValueError("handoff test labels do not match cached scores")
        medians = train_runtime_medians(train)
        frame = build_handoff_frame(
            test, proba, medians, threshold=args.threshold
        )
        write_handoff_parquet(frame, args.handoff)
        summary = handoff_summary(frame, threshold=args.threshold)
        manifest["handoff"] = {
            "path": repo_relpath(args.handoff),
            "sha256": sha256_file(args.handoff),
            "bytes": args.handoff.stat().st_size,
            **summary,
        }

    if not args.skip_model:
        model_meta = persist_frozen_model(
            scores_path=args.scores, bundle_path=args.bundle
        )
        model_meta["bundle_path"] = repo_relpath(args.bundle)
        model_meta["sha256"] = sha256_file(args.bundle)
        model_meta["bytes"] = args.bundle.stat().st_size
        manifest["model"] = model_meta
        FREEZE_META_PATH.write_text(
            json.dumps(model_meta, indent=2, default=str), encoding="utf-8"
        )
        print(f"[ok] wrote {FREEZE_META_PATH}")

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(json.dumps(manifest, indent=2, default=str))
    print(f"[ok] wrote {MANIFEST_PATH}")
    if not args.skip_handoff:
        print(f"[ok] wrote {args.handoff}")
    if not args.skip_model:
        print(f"[ok] wrote {args.bundle}")


if __name__ == "__main__":
    main()
