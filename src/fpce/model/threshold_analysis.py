"""Descriptive threshold / PR analysis for the primary HistGB baseline.

Does not train a new classifier family. If test scores were not persisted,
refits the *same* HistGB spec (``HGB_PARAMS`` + ``random_state=0``) only to
recover probabilities.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from fpce.config import REPORTS_DIR
from fpce.model.evaluate import operational_metrics, select_operating_point

DEFAULT_THRESHOLDS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
SCORES_PATH = REPORTS_DIR / "primary_hgb_test_scores.npz"
REPORT_PATH = REPORTS_DIR / "primary_hgb_thresholds.json"
FIGURES_DIR = REPORTS_DIR / "figures"
PERCENTILES = (1, 10, 25, 50, 75, 90, 95, 99)


def scores_path() -> Path:
    return SCORES_PATH


def save_test_scores(y_test: np.ndarray, proba: np.ndarray, path: Path | None = None) -> Path:
    path = path or SCORES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        y_test=np.asarray(y_test, dtype=np.int8),
        proba=np.asarray(proba, dtype=np.float32),
    )
    return path


def load_test_scores(path: Path | None = None) -> tuple[np.ndarray, np.ndarray]:
    payload = np.load(path or SCORES_PATH)
    return payload["y_test"].astype(int), payload["proba"].astype(float)


def recover_test_scores() -> tuple[np.ndarray, np.ndarray, str]:
    """Load cached test probabilities, or refit the documented HistGB once."""
    if SCORES_PATH.exists():
        y_test, proba = load_test_scores()
        return y_test, proba, "cached"
    from fpce.features.assemble import prepare_primary_training
    from fpce.model.train import fit_hist_gb, prepare_design_matrices

    prepared = prepare_primary_training()
    x_train, x_test, y_train, y_test, _, _ = prepare_design_matrices(prepared)
    model, _ = fit_hist_gb(x_train, y_train)
    proba = model.predict_proba(x_test)[:, 1]
    save_test_scores(y_test, proba)
    return y_test, proba, "refit_same_hgb_params"


def score_percentiles(y_true: np.ndarray, scores: np.ndarray) -> dict:
    y_true = np.asarray(y_true, dtype=int)
    scores = np.asarray(scores, dtype=float)

    def _one(mask: np.ndarray) -> dict:
        s = scores[mask]
        qs = {f"p{p}": float(np.percentile(s, p)) for p in PERCENTILES}
        return {
            "n": int(mask.sum()),
            "min": float(s.min()) if len(s) else None,
            "max": float(s.max()) if len(s) else None,
            "mean": float(s.mean()) if len(s) else None,
            **qs,
        }

    return {
        "percentiles": list(PERCENTILES),
        "positives": _one(y_true == 1),
        "negatives": _one(y_true == 0),
    }


def sweep_thresholds(
    y_true: np.ndarray,
    scores: np.ndarray,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> list[dict]:
    return [operational_metrics(y_true, scores, t) for t in thresholds]


def operating_points(y_true: np.ndarray, scores: np.ndarray) -> dict:
    return {
        "recall_ge_0.90_max_precision": select_operating_point(
            y_true, scores, min_recall=0.90
        ),
        "recall_ge_0.80_max_precision": select_operating_point(
            y_true, scores, min_recall=0.80
        ),
        "precision_ge_0.10_max_recall": select_operating_point(
            y_true, scores, min_precision=0.10
        ),
        "precision_ge_0.25_max_recall": select_operating_point(
            y_true, scores, min_precision=0.25
        ),
        "note": (
            "These points are read off the test PR curve for description only. "
            "They were not used to refit the model."
        ),
    }


def _plot_pr_curve(
    y_true: np.ndarray,
    scores: np.ndarray,
    path: Path,
    marks: list[tuple[float, str]],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from sklearn.metrics import average_precision_score, precision_recall_curve

    precision, recall, _ = precision_recall_curve(y_true, scores)
    ap = float(average_precision_score(y_true, scores))
    prevalence = float(np.mean(y_true))
    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.plot(recall, precision, color="#1f4e79", lw=2, label=f"HistGB (AP={ap:.3f})")
    ax.axhline(prevalence, color="#888888", ls="--", lw=1, label=f"prevalence={prevalence:.4f}")
    for threshold, label in marks:
        row = operational_metrics(y_true, scores, threshold)
        ax.scatter(
            row["recall"],
            row["precision"],
            zorder=5,
            s=40,
            label=f"{label} (t={threshold:.2f})",
        )
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_title("Primary rack HistGB — Precision-Recall (test)")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_threshold_pr(
    y_true: np.ndarray,
    scores: np.ndarray,
    path: Path,
    grid: np.ndarray,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = [operational_metrics(y_true, scores, float(t)) for t in grid]
    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.plot(grid, [r["precision"] for r in rows], color="#1f4e79", lw=2, label="Precision")
    ax.plot(grid, [r["recall"] for r in rows], color="#c45911", lw=2, label="Recall")
    ax.axvline(0.5, color="#888888", ls="--", lw=1, label="t=0.5")
    ax.set_xlabel("Threshold")
    ax.set_ylabel("Score")
    ax.set_title("Primary rack HistGB — threshold vs precision / recall (test)")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def build_report(
    y_true: np.ndarray,
    scores: np.ndarray,
    *,
    scores_source: str,
) -> dict:
    sweep = sweep_thresholds(y_true, scores)
    at_half = next(r for r in sweep if abs(r["threshold"] - 0.5) < 1e-12)
    ops = operating_points(y_true, scores)
    return {
        "scores_source": scores_source,
        "n_test": int(len(y_true)),
        "n_positive": int(np.asarray(y_true).sum()),
        "grid": sweep,
        "operating_points": ops,
        "score_distribution": score_percentiles(y_true, scores),
        "reference_threshold_0.5": {
            "fp_per_tp": at_half["fp_per_tp"],
            "n_alerts": at_half["n_alerts"],
            "recall": at_half["recall"],
            "precision": at_half["precision"],
        },
    }


def write_figures(
    y_true: np.ndarray,
    scores: np.ndarray,
    report: dict,
) -> dict[str, str]:
    pr_path = FIGURES_DIR / "primary_hgb_pr_curve.png"
    thr_path = FIGURES_DIR / "primary_hgb_threshold_precision_recall.png"
    marks = [(0.5, "t=0.5")]
    rec90 = report["operating_points"]["recall_ge_0.90_max_precision"]
    prec10 = report["operating_points"]["precision_ge_0.10_max_recall"]
    if rec90 is not None:
        marks.append((rec90["threshold"], "recall≥0.90"))
    if prec10 is not None:
        marks.append((prec10["threshold"], "precision≥0.10"))
    _plot_pr_curve(y_true, scores, pr_path, marks)
    _plot_threshold_pr(y_true, scores, thr_path, np.linspace(0.05, 0.95, 37))
    return {
        "pr_curve": str(pr_path.relative_to(REPORTS_DIR.parent)),
        "threshold_vs_pr": str(thr_path.relative_to(REPORTS_DIR.parent)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Threshold sweep for the primary HistGB baseline (no new model)."
    )
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    y_test, proba, source = recover_test_scores()
    report = build_report(y_test, proba, scores_source=source)
    report["figures"] = write_figures(y_test, proba, report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"[ok] wrote {args.output}")


if __name__ == "__main__":
    main()
