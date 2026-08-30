"""Lead time vs reactive baseline for the primary HistGB working threshold.

Does not train a new model. Aligns ``reports/primary_hgb_test_scores.npz`` with
the frozen primary test split and scores alerts at admission only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fpce.config import REPORTS_DIR, resolve_repo_path
from fpce.contracts import load_feature_contract
from fpce.features.assemble import load_time_split, load_trainable_events
from fpce.model.baseline import reactive_fire_time, train_runtime_medians
from fpce.model.threshold_analysis import SCORES_PATH, load_test_scores

WORKING_THRESHOLD = 0.9
LEAD_BINS_SECONDS = (
    ("lt_1_min", 0, 60),
    ("min_1_to_5", 60, 300),
    ("min_5_to_15", 300, 900),
    ("min_15_to_30", 900, 1800),
    ("gt_30_min", 1800, None),
)
LEAD_PERCENTILES = (10, 25, 50, 75, 90, 95)
META_COLUMNS = (
    "instance_name",
    "event_end",
    "end_time",
    "seq_no",
    "task_type",
    "waste_window_seconds",
    "eligible_for_costing",
)
FIGURES_DIR = REPORTS_DIR / "figures"
REPORT_PATH = REPORTS_DIR / "primary_hgb_lead_time.json"


def _duration_stats(values: np.ndarray) -> dict:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return {
            "n": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            **{f"p{p}": None for p in LEAD_PERCENTILES},
        }
    qs = {f"p{p}": float(np.percentile(values, p)) for p in LEAD_PERCENTILES}
    return {
        "n": int(len(values)),
        "min": float(values.min()),
        "max": float(values.max()),
        "mean": float(values.mean()),
        "median": float(np.median(values)),
        **qs,
    }


def _bin_counts(values: np.ndarray) -> dict[str, int]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    out: dict[str, int] = {}
    for name, lo, hi in LEAD_BINS_SECONDS:
        if hi is None:
            out[name] = int((values >= lo).sum())
        else:
            out[name] = int(((values >= lo) & (values < hi)).sum())
    out["n"] = int(len(values))
    return out


def alert_before_failure(alert_time: pd.Series, event_end: pd.Series) -> pd.Series:
    alert = pd.to_numeric(alert_time, errors="coerce")
    end = pd.to_numeric(event_end, errors="coerce")
    return alert.notna() & end.notna() & (alert < end)


def lead_seconds(alert_time: pd.Series, event_end: pd.Series) -> pd.Series:
    """failure_time - alert_time; only when alert is strictly before failure."""
    alert = pd.to_numeric(alert_time, errors="coerce")
    end = pd.to_numeric(event_end, errors="coerce")
    ok = alert_before_failure(alert, end)
    lead = (end - alert).where(ok, pd.NA)
    return lead


def load_split_events() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    contract = load_feature_contract()
    split = load_time_split()
    events_path = resolve_repo_path(split["instance_events_path"])
    events = load_trainable_events(events_path, contract, extra_columns=META_COLUMNS)
    split_ts = int(split["split_timestamp"])
    train = events.loc[events[contract.split_column] < split_ts].reset_index(drop=True)
    test = events.loc[events[contract.split_column] >= split_ts].reset_index(drop=True)
    return train, test, split


def attach_scores(test: pd.DataFrame, scores_path: Path = SCORES_PATH) -> pd.DataFrame:
    y_npz, proba = load_test_scores(scores_path)
    if len(test) != len(y_npz):
        raise ValueError(
            f"test rows ({len(test)}) != saved scores ({len(y_npz)}); "
            "cannot align without refitting"
        )
    y_test = pd.to_numeric(test["failed"], errors="coerce").fillna(0).astype(int).to_numpy()
    if not np.array_equal(y_test, y_npz):
        raise ValueError("saved y_test does not match frozen test labels; refusing to join")
    out = test.copy()
    out["proba"] = proba
    return out


def summarize_lead(name: str, lead: pd.Series, n_failures: int, n_detected: int) -> dict:
    arr = pd.to_numeric(lead, errors="coerce").dropna().to_numpy()
    return {
        "name": name,
        "n_failures": int(n_failures),
        "n_detected_before_event": int(n_detected),
        "n_not_detected": int(n_failures - n_detected),
        "pct_detected": (100.0 * n_detected / n_failures) if n_failures else 0.0,
        "lead_time_seconds": _duration_stats(arr),
        "lead_time_bins": _bin_counts(arr),
    }


def build_lead_time_report(
    *,
    threshold: float = WORKING_THRESHOLD,
    scores_path: Path = SCORES_PATH,
) -> dict:
    train, test, split = load_split_events()
    test = attach_scores(test, scores_path)
    failures = test.loc[test["failed"] == 1].copy()
    n_fail = len(failures)

    model_alert = failures["decision_time"].where(failures["proba"] >= threshold, pd.NA)
    model_detected = alert_before_failure(model_alert, failures["event_end"])
    model_lead = lead_seconds(model_alert, failures["event_end"])

    medians = train_runtime_medians(train)
    base_fire = reactive_fire_time(failures, medians)
    base_detected = alert_before_failure(base_fire, failures["event_end"])
    base_lead = lead_seconds(base_fire, failures["event_end"])

    both = model_detected & base_detected
    delta = (
        pd.to_numeric(model_lead, errors="coerce")
        - pd.to_numeric(base_lead, errors="coerce")
    ).where(both, pd.NA)
    # Positive delta: model has more remaining time until failure = fired earlier.

    model_summary = summarize_lead("hist_gb", model_lead, n_fail, int(model_detected.sum()))
    base_summary = summarize_lead("reactive", base_lead, n_fail, int(base_detected.sum()))
    return {
        "threshold": float(threshold),
        "split_timestamp": int(split["split_timestamp"]),
        "scores_path": str(scores_path.as_posix()),
        "definitions": {
            "failure": "test row with failed=1 (Failed/Interrupted), eligible_for_training=1",
            "failure_time": "event_end (recorded end_time, else start_time if end_time=0)",
            "model_alert_time": "decision_time if proba >= threshold, else no alert",
            "lead_time": "failure_time - alert_time, only if alert_time < failure_time",
            "reactive": (
                "min(retry at decision_time if seq_no>=2, "
                "decision_time + train median succeeded duration by task_type)"
            ),
            "delta": "lead_time_model - lead_time_baseline on failures both detect before event_end",
        },
        "n_test": int(len(test)),
        "reactive_runtime_medians_seconds": {
            "global": float(medians.attrs.get("global_median", 0.0)),
            "by_task_type": {
                str(k): (None if pd.isna(v) else float(v)) for k, v in medians.items()
            },
        },
        "model": model_summary,
        "baseline": base_summary,
        "paired": {
            "n_both_detected": int(both.sum()),
            "n_model_only": int((model_detected & ~base_detected).sum()),
            "n_baseline_only": int((base_detected & ~model_detected).sum()),
            "delta_seconds": _duration_stats(
                pd.to_numeric(delta, errors="coerce").dropna().to_numpy()
            ),
        },
        "n_failures_zero_or_missing_window": int(
            (
                pd.to_numeric(failures["event_end"], errors="coerce").isna()
                | (
                    pd.to_numeric(failures["event_end"], errors="coerce")
                    <= pd.to_numeric(failures["decision_time"], errors="coerce")
                )
            ).sum()
        ),
        "_leads": {
            "model": pd.to_numeric(model_lead, errors="coerce").to_numpy(),
            "baseline": pd.to_numeric(base_lead, errors="coerce").to_numpy(),
        },
    }


def _plot_lead_hist(model_lead: np.ndarray, baseline_lead: np.ndarray, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def minutes(x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        return x[np.isfinite(x)] / 60.0

    m = minutes(model_lead)
    b = minutes(baseline_lead)
    fig, ax = plt.subplots(figsize=(8, 5.2))
    bins = [0, 1, 5, 15, 30, 60, 120, 240]
    ax.hist(m, bins=bins, alpha=0.65, label="HistGB t=0.9", color="#1f4e79")
    ax.hist(b, bins=bins, alpha=0.45, label="Reactive baseline", color="#c45911")
    ax.set_xlabel("Lead time (minutes)")
    ax.set_ylabel("Failures detected before event_end")
    ax.set_title("Primary rack — lead time on test failures")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lead time at HistGB working threshold 0.9")
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument("--threshold", type=float, default=WORKING_THRESHOLD)
    args = parser.parse_args()
    report = build_lead_time_report(threshold=args.threshold)
    leads = report.pop("_leads")
    fig_path = FIGURES_DIR / "primary_hgb_lead_time.png"
    _plot_lead_hist(leads["model"], leads["baseline"], fig_path)
    report["figure"] = str(fig_path.as_posix())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"[ok] wrote {args.output}")


if __name__ == "__main__":
    main()
