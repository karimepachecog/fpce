"""Train on Alibaba instance events, score on a Google 2019 attempt table.

Uses only the intersection of `params/feature_contract.json` allow-list columns
that exist in both tables after unit alignment. Host time-grid features are
omitted unless both sides provide them (the Google MVP export does not).

`machine_id` is excluded even if allow-listed: Alibaba ids are strings like
`m_1029` and Google ids are int64, so `pd.to_numeric` turns the training
column into all-NaN and the comparison is meaningless.

Raw `plan_cpu` / `plan_mem` are also excluded once `*_frac` columns exist.
Alibaba `plan_cpu` is hundredths of a core on a 96-thread machine; Google
`cpus_request` is already a fraction of the largest machine in the cell.
Comparing them without `plan_cpu_frac` is an 8,000× scale artifact.

EVICT/KILL Google rows are already `eligible_for_training=0` in the adapter,
so the comparison is FAIL vs FINISH against Alibaba Failed vs Terminated.

Alibaba prevalence is ~0.17%; Google FAIL/(FAIL+FINISH) at attempt level is
~18%. F1 at threshold 0.5 therefore measures base-rate shift, not
distribution shift. The headline metrics are ROC-AUC, PR-AUC, and
lift = PR-AUC / base rate. An equalized-prevalence variant downsamples
Google positives to the Alibaba rate and applies a threshold calibrated
on the Alibaba test split.

This is a Role B evaluation helper, not a substitute for the full classifier.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fpce.config import (
    ALI_CPU_NUM,
    ALI_PLAN_CPU_HUNDREDTHS,
    DATA_PROCESSED,
    GOOGLE_ATTEMPTS_NAME,
    GOOGLE_ATTEMPTS_SAMPLE_NAME,
    RACKS,
    REPORTS_DIR,
    repo_relpath,
    resolve_repo_path,
)
from fpce.contracts import load_feature_contract

try:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import (
        average_precision_score,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )
except ImportError as exc:  # pragma: no cover - optional extra
    HistGradientBoostingClassifier = None  # type: ignore[misc, assignment]
    _SKLEARN_IMPORT_ERROR = exc
else:
    _SKLEARN_IMPORT_ERROR = None

CROSS_PROVIDER_EXCLUDE = frozenset({"machine_id"})
RAW_WHEN_FRAC = {
    "plan_cpu_frac": "plan_cpu",
    "plan_mem_frac": "plan_mem",
}


def ensure_plan_frac(df: pd.DataFrame, provider: str) -> pd.DataFrame:
    """Add machine-fraction columns if the caller did not already emit them."""
    out = df
    need_cpu = "plan_cpu_frac" not in out.columns
    need_mem = "plan_mem_frac" not in out.columns
    if not need_cpu and not need_mem:
        return out
    out = out.copy()
    if need_cpu:
        raw = out["plan_cpu"] if "plan_cpu" in out.columns else out.get("cpus_request")
        cpu = pd.to_numeric(raw, errors="coerce") if raw is not None else pd.Series(pd.NA, index=out.index)
        if provider == "alibaba":
            out["plan_cpu_frac"] = cpu / ALI_PLAN_CPU_HUNDREDTHS / ALI_CPU_NUM
        else:
            out["plan_cpu_frac"] = cpu
    if need_mem:
        raw = out["plan_mem"] if "plan_mem" in out.columns else out.get("memory_request")
        mem = pd.to_numeric(raw, errors="coerce") if raw is not None else pd.Series(pd.NA, index=out.index)
        out["plan_mem_frac"] = mem
    return out


def _with_retry_index(df: pd.DataFrame) -> pd.DataFrame:
    if "retry_index" in df.columns:
        return df
    if "seq_no" in df.columns:
        out = df.copy()
        out["retry_index"] = pd.to_numeric(out["seq_no"], errors="coerce")
        return out
    if "attempt_index" in df.columns:
        out = df.copy()
        out["retry_index"] = pd.to_numeric(out["attempt_index"], errors="coerce")
        return out
    return df


def shared_feature_columns(
    alibaba: pd.DataFrame,
    google: pd.DataFrame,
) -> list[str]:
    contract = load_feature_contract()
    allowed = set(contract.allow)
    shared = [c for c in alibaba.columns if c in allowed and c in google.columns]
    shared = [c for c in shared if c not in CROSS_PROVIDER_EXCLUDE]
    for frac, raw in RAW_WHEN_FRAC.items():
        if frac in shared:
            shared = [c for c in shared if c != raw]
    contract.assert_no_leakage(shared)
    if not shared:
        raise ValueError(
            "No overlapping allowed features between Alibaba and Google tables"
        )
    return shared


def _xy(df: pd.DataFrame, columns: list[str]) -> tuple[pd.DataFrame, np.ndarray]:
    trainable = df[df["eligible_for_training"] == 1]
    x = trainable[columns].apply(pd.to_numeric, errors="coerce")
    y = trainable["failed"].to_numpy(dtype=int)
    return x, y


def _ranking_metrics(y_true: np.ndarray, proba: np.ndarray) -> dict:
    base = float(np.mean(y_true)) if len(y_true) else 0.0
    metrics: dict = {
        "n": int(len(y_true)),
        "positive_rate_pct": round(base * 100, 4),
    }
    if len(y_true) and y_true.min() != y_true.max():
        roc = float(roc_auc_score(y_true, proba))
        pr = float(average_precision_score(y_true, proba))
        metrics["roc_auc"] = round(roc, 4)
        metrics["pr_auc"] = round(pr, 4)
        metrics["lift"] = round(pr / base, 4) if base else None
    else:
        metrics["roc_auc"] = None
        metrics["pr_auc"] = None
        metrics["lift"] = None
    return metrics


def _threshold_metrics(
    y_true: np.ndarray,
    proba: np.ndarray,
    threshold: float,
    *,
    confounded_by_prevalence: bool,
) -> dict:
    pred = (proba >= threshold).astype(int)
    return {
        "n": int(len(y_true)),
        "positive_rate_pct": round(float(np.mean(y_true) * 100) if len(y_true) else 0.0, 4),
        "threshold": round(float(threshold), 4),
        "precision": round(float(precision_score(y_true, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, pred, zero_division=0)), 4),
        "confounded_by_prevalence": confounded_by_prevalence,
    }


def _calibrate_threshold(y_true: np.ndarray, proba: np.ndarray) -> float:
    """F1-maximising threshold on the calibration split (Alibaba test)."""
    if len(y_true) == 0 or y_true.min() == y_true.max():
        return 0.5
    candidates = np.unique(np.quantile(proba, np.linspace(0.01, 0.99, 99)))
    best_t, best_f1 = 0.5, -1.0
    for threshold in candidates:
        pred = (proba >= threshold).astype(int)
        score = float(f1_score(y_true, pred, zero_division=0))
        if score > best_f1:
            best_t, best_f1 = float(threshold), score
    return best_t


def match_prevalence(
    x: pd.DataFrame,
    y: np.ndarray,
    target_rate: float,
    random_state: int = 0,
) -> tuple[pd.DataFrame, np.ndarray]:
    """Downsample positives so the positive rate matches `target_rate`."""
    if target_rate <= 0 or target_rate >= 1 or len(y) == 0:
        return x, y
    pos = np.flatnonzero(y == 1)
    neg = np.flatnonzero(y == 0)
    if len(neg) == 0 or len(pos) == 0:
        return x, y
    n_pos = int(round(target_rate / (1.0 - target_rate) * len(neg)))
    n_pos = max(1, min(n_pos, len(pos)))
    rng = np.random.default_rng(random_state)
    take_pos = rng.choice(pos, size=n_pos, replace=False)
    idx = np.concatenate([take_pos, neg])
    rng.shuffle(idx)
    return x.iloc[idx], y[idx]


def evaluate_cross_provider(
    alibaba: pd.DataFrame,
    google: pd.DataFrame,
    split_timestamp: int | None = None,
    max_train_rows: int | None = 200_000,
    random_state: int = 0,
) -> dict:
    if HistGradientBoostingClassifier is None:
        raise ImportError(
            "scikit-learn is required for fpce-cross-provider "
            f"({_SKLEARN_IMPORT_ERROR})"
        )
    ali = _with_retry_index(ensure_plan_frac(alibaba, "alibaba"))
    goog = _with_retry_index(ensure_plan_frac(google, "google"))
    columns = shared_feature_columns(ali, goog)
    if split_timestamp is not None and "start_time" in ali.columns:
        train_df = ali[ali["start_time"] < split_timestamp]
        test_df = ali[ali["start_time"] >= split_timestamp]
    else:
        train_df, test_df = ali, ali.iloc[0:0]

    x_train, y_train = _xy(train_df, columns)
    if max_train_rows is not None and len(x_train) > max_train_rows:
        rng = np.random.default_rng(random_state)
        pos = np.flatnonzero(y_train == 1)
        neg = np.flatnonzero(y_train == 0)
        n_neg = max(max_train_rows - len(pos), 0)
        take_neg = rng.choice(neg, size=min(n_neg, len(neg)), replace=False)
        idx = np.concatenate([pos, take_neg])
        rng.shuffle(idx)
        x_train, y_train = x_train.iloc[idx], y_train[idx]

    model = HistGradientBoostingClassifier(random_state=random_state, max_depth=6)
    model.fit(x_train, y_train)

    report: dict = {
        "features": columns,
        "excluded": sorted(CROSS_PROVIDER_EXCLUDE),
        "n_train": int(len(y_train)),
        "train_positive_rate_pct": round(float(np.mean(y_train) * 100), 4),
        "notes": (
            "Headline metrics are ROC-AUC / PR-AUC / lift. "
            "F1 at threshold 0.5 is reported as confounded by the ~109× "
            "prevalence gap. machine_id is never a cross-provider feature."
        ),
    }

    ali_test_proba = None
    y_test = np.array([], dtype=int)
    if len(test_df):
        x_test, y_test = _xy(test_df, columns)
        if len(y_test):
            ali_test_proba = model.predict_proba(x_test)[:, 1]
            ranking = _ranking_metrics(y_test, ali_test_proba)
            thresholded = _threshold_metrics(
                y_test, ali_test_proba, 0.5, confounded_by_prevalence=False
            )
            report["alibaba_test"] = {**ranking, "at_0_5": thresholded}

    x_g, y_g = _xy(goog, columns)
    if len(y_g):
        goog_proba = model.predict_proba(x_g)[:, 1]
        ranking = _ranking_metrics(y_g, goog_proba)
        thresholded = _threshold_metrics(
            y_g, goog_proba, 0.5, confounded_by_prevalence=True
        )
        report["google"] = {**ranking, "at_0_5": thresholded}
        if "alibaba_test" in report and report["alibaba_test"].get("roc_auc") is not None:
            report["roc_auc_drop"] = round(
                report["alibaba_test"]["roc_auc"] - ranking["roc_auc"], 4
            )
            report["lift_drop"] = round(
                (report["alibaba_test"]["lift"] or 0) - (ranking["lift"] or 0), 4
            )
            report["f1_at_0_5"] = {
                "alibaba_test": report["alibaba_test"]["at_0_5"]["f1"],
                "google": thresholded["f1"],
                "note": "confounded by prevalence; do not cite as shift",
            }

        target_rate = float(np.mean(y_train)) if len(y_train) else 0.00168
        x_eq, y_eq = match_prevalence(x_g, y_g, target_rate, random_state)
        if len(y_eq):
            eq_proba = model.predict_proba(x_eq)[:, 1]
            calibrated = (
                _calibrate_threshold(y_test, ali_test_proba)
                if ali_test_proba is not None
                else 0.5
            )
            eq_rank = _ranking_metrics(y_eq, eq_proba)
            eq_thr = _threshold_metrics(
                y_eq, eq_proba, calibrated, confounded_by_prevalence=False
            )
            report["google_equalized_prevalence"] = {
                **eq_rank,
                "target_positive_rate_pct": round(target_rate * 100, 4),
                "calibrated_threshold": round(float(calibrated), 4),
                "at_calibrated_threshold": eq_thr,
            }
    return report


def _load_alibaba_events(path: Path) -> pd.DataFrame:
    """Project allowed columns so a laptop does not load 13M wide rows."""
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    wanted = [
        "plan_cpu",
        "plan_mem",
        "plan_cpu_frac",
        "plan_mem_frac",
        "seq_no",
        "retry_index",
        "failed",
        "eligible_for_training",
        "start_time",
    ]
    dataset = ds.dataset(str(path), format="parquet")
    available = [name for name in wanted if name in dataset.schema.names]
    table = dataset.to_table(
        columns=available,
        filter=pc.field("eligible_for_training") == 1,
    )
    return table.to_pandas()


def _load_split_timestamp(path: Path) -> int | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return int(payload["split_timestamp"])


def _load_google_attempts(path: Path) -> pd.DataFrame:
    """Project allowed columns and keep only trainable rows."""
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    wanted = [
        "plan_cpu",
        "plan_mem",
        "plan_cpu_frac",
        "plan_mem_frac",
        "attempt_index",
        "retry_index",
        "failed",
        "eligible_for_training",
        "start_time",
        "seq_no",
    ]
    dataset = ds.dataset(path, format="parquet")
    available = [name for name in wanted if name in dataset.schema.names]
    table = dataset.to_table(
        columns=available,
        filter=pc.field("eligible_for_training") == 1,
    )
    return table.to_pandas()


def _default_google_path() -> Path:
    """Prefer the laptop sample when present so Role B does not load 18.6M rows."""
    sample = Path(RACKS["google"]["output_dir"]) / GOOGLE_ATTEMPTS_SAMPLE_NAME
    full = Path(RACKS["google"]["output_dir"]) / GOOGLE_ATTEMPTS_NAME
    return sample if sample.exists() else full


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train on Alibaba, evaluate on Google attempts"
    )
    parser.add_argument(
        "--alibaba",
        type=Path,
        default=Path(RACKS["primary"]["output_dir"]) / "instance_events.parquet",
    )
    parser.add_argument(
        "--google",
        type=Path,
        default=_default_google_path(),
        help="Google attempts parquet (defaults to attempts_sample.parquet if present)",
    )
    parser.add_argument(
        "--split",
        type=Path,
        default=DATA_PROCESSED / "primary_time_split.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPORTS_DIR / "cross_provider.json",
    )
    parser.add_argument("--max-train-rows", type=int, default=200_000)
    args = parser.parse_args()
    args.alibaba = resolve_repo_path(args.alibaba)
    args.google = resolve_repo_path(args.google)
    args.split = resolve_repo_path(args.split)

    if not args.alibaba.exists():
        raise SystemExit(f"Missing Alibaba events: {args.alibaba}")
    if not args.google.exists():
        raise SystemExit(
            f"Missing Google attempts: {args.google}\n"
            "Run: fpce-google-events --input data/raw/google"
        )

    alibaba = _load_alibaba_events(args.alibaba)
    google = _load_google_attempts(args.google)
    report = evaluate_cross_provider(
        alibaba,
        google,
        split_timestamp=_load_split_timestamp(args.split),
        max_train_rows=args.max_train_rows,
    )
    report["role"] = "A_adapter_smoke_test"
    report["google_path"] = repo_relpath(args.google)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[ok] cross-provider eval -> {args.output}")
    print(f"[ok] google table: {args.google}")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
