"""Symmetric policy accounting, threshold sweep, and figures.

Killing a healthy job is destroyed useful work, not avoided waste.
Both the model and the reactive baseline are charged that cost.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from fpce.config import REPORTS_DIR
from fpce.costing.coefficients import load_physical_cost_params
from fpce.replay.policy import (
    DEFAULT_COSTING,
    DEFAULT_GRID,
    DEFAULT_HANDOFF,
    DEFAULT_SUMMARY,
    RANGE_KEYS,
    TEST_FALSE_POSITIVES_AT_WORKING_THRESHOLD,
    empty_range,
    sweep_window,
    utilization_fraction,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_FIGURE = REPORTS_DIR / "figures" / "policy_simulation.png"
DEFAULT_SWEEP = REPORTS_DIR / "policy_threshold_sweep.json"
PRIMARY_MEAN_CPU_FRAC = 0.4072
TRAIN_SUCCESS_MEDIAN_SECONDS = 10.0
BASELINE_SAMPLE_N = 50_000
BASELINE_SAMPLE_SEED = 0
SWEEP_THRESHOLDS = (0.5, 0.9, 0.99, 0.999, 0.9999)
SWEEP_MIN_SCORE = 0.5
POOL_ARTIFACT_NOTE = (
    "The costing pool is failed jobs with a measured waste window >= 60 s. "
    "The reactive baseline fires at decision_time + train-success median "
    "(global 10 s, or by task_type). On that pool it almost always arrives "
    "in time (203/204). That is a selection artifact, not evidence that "
    "the baseline is a strong detector."
)
RUNNER_NOTE = (
    "src/fpce/replay/runner.py independently costs the 204-row eligible "
    "pool. reports/replay_summary.json n_costed_rows=204 and the IT / "
    "facility / water ranges match policy_simulation.json do_nothing. "
    "Headline avoided-vs-destroyed accounting remains fpce-policy-sim + "
    "fpce-policy-report."
)


def napkin_fp_cost(
    n_fp: int,
    *,
    dt_seconds: float,
    u_mean: float,
    corners: list[dict[str, float]],
) -> dict[str, float]:
    """n_fp identical windows at constant utilization (order-of-magnitude only)."""
    if n_fp <= 0 or dt_seconds <= 0:
        return empty_range(n_corners=len(corners))
    one = sweep_window(np.array([float(u_mean)]), float(dt_seconds), corners)
    return {key: float(one[key]) * int(n_fp) for key in RANGE_KEYS}


def _scale_range(values: dict[str, float], factor: float) -> dict[str, float]:
    return {key: float(values[key]) * float(factor) for key in RANGE_KEYS}


def net_range(avoided: dict[str, float], destroyed: dict[str, float]) -> dict[str, float]:
    """Pessimistic min = avoided_min - destroyed_max; optimistic max is the reverse."""
    out: dict[str, float] = {}
    for metric in ("it_kwh", "facility_kwh", "water_liters"):
        out[f"{metric}_min"] = float(avoided[f"{metric}_min"]) - float(
            destroyed[f"{metric}_max"]
        )
        out[f"{metric}_max"] = float(avoided[f"{metric}_max"]) - float(
            destroyed[f"{metric}_min"]
        )
    return out


def cost_alerted_successes(
    handoff: pd.DataFrame,
    grid: pd.DataFrame,
    corners: list[dict[str, float]],
    *,
    alert_col: str = "model_alert",
    start_col: str = "decision_time",
    sample_n: int | None = None,
    sample_seed: int = BASELINE_SAMPLE_SEED,
) -> tuple[dict[str, float], dict]:
    """Fan ranges if every alerted success is killed at ``start_col``."""
    fps = handoff.copy()
    if "failed" in fps.columns:
        fps = fps.loc[
            pd.to_numeric(fps["failed"], errors="coerce").fillna(0).astype(int) == 0
        ]
    if alert_col in fps.columns:
        fps = fps.loc[
            pd.to_numeric(fps[alert_col], errors="coerce").fillna(0).astype(int) == 1
        ]
    fps = fps.reset_index(drop=True)
    n_total = int(len(fps))

    scale = 1.0
    n_sampled = n_total
    if sample_n is not None and n_total > sample_n:
        fps = fps.sample(n=sample_n, random_state=sample_seed).reset_index(drop=True)
        n_sampled = int(len(fps))
        scale = n_total / n_sampled

    per_row, stats = _cost_success_windows(fps, grid, corners, start_col=start_col)
    totals = {key: float(per_row[key].sum()) * scale for key in RANGE_KEYS}
    stats["n_false_positives"] = n_total
    stats["n_sampled"] = n_sampled
    stats["scale_factor"] = float(scale)
    stats["alert_col"] = alert_col
    stats["start_col"] = start_col
    return totals, stats


def _cost_success_windows(
    fps: pd.DataFrame,
    grid: pd.DataFrame,
    corners: list[dict[str, float]],
    *,
    start_col: str,
) -> tuple[pd.DataFrame, dict]:
    grid_by_machine = {
        machine_id: group.sort_values("time_stamp")
        for machine_id, group in grid.groupby("machine_id", sort=False)
    }
    records: list[dict] = []
    durations: list[float] = []
    n_positive = 0
    for _, row in fps.iterrows():
        start = pd.to_numeric(pd.Series([row[start_col]]), errors="coerce").iloc[0]
        event_end = pd.to_numeric(pd.Series([row["event_end"]]), errors="coerce").iloc[0]
        if pd.isna(start) or pd.isna(event_end):
            continue
        dt = float(event_end) - float(start)
        if dt <= 0:
            continue
        n_positive += 1
        durations.append(dt)
        cost = sweep_window(
            utilization_fraction(
                grid_by_machine.get(row["machine_id"]), float(start), float(event_end)
            ),
            dt,
            corners,
        )
        rec = {key: float(cost[key]) for key in RANGE_KEYS}
        rec["duration_seconds"] = dt
        if "model_score" in row.index:
            rec["model_score"] = float(row["model_score"])
        if "test_row_index" in row.index and pd.notna(row["test_row_index"]):
            rec["test_row_index"] = int(row["test_row_index"])
        records.append(rec)

    per_row = pd.DataFrame(records)
    if per_row.empty:
        per_row = pd.DataFrame(columns=[*RANGE_KEYS, "duration_seconds", "model_score"])
    stats = {
        "n_with_positive_window": n_positive,
        "n_zero_or_missing_window": int(len(fps) - n_positive),
        "duration_seconds_median": (
            float(np.median(durations)) if durations else None
        ),
        "duration_seconds_p90": (
            float(np.quantile(durations, 0.90)) if durations else None
        ),
    }
    return per_row, stats


def _sum_score_ge(per_row: pd.DataFrame, threshold: float) -> dict[str, float]:
    if per_row.empty or "model_score" not in per_row.columns:
        return {key: 0.0 for key in RANGE_KEYS}
    mask = pd.to_numeric(per_row["model_score"], errors="coerce") >= float(threshold)
    return {key: float(per_row.loc[mask, key].sum()) for key in RANGE_KEYS}


def attach_policy_nets(
    summary: dict,
    *,
    model_destroyed: dict[str, float],
    baseline_destroyed: dict[str, float],
    model_fp_stats: dict,
    baseline_fp_stats: dict,
) -> dict:
    """Add destroyed / net / alert counts to each kill policy."""
    headline = (
        "Both admission-kill policies are net-negative on the 204-row costing "
        "pool. The model is ~100× less bad than the reactive baseline because "
        "it fires on 0.6% of test rows instead of 46.5%."
    )
    summary["headline"] = headline
    summary["pool_artifact_note"] = POOL_ARTIFACT_NOTE
    summary["runner_note"] = RUNNER_NOTE

    for name, destroyed, stats in (
        ("model_policy", model_destroyed, model_fp_stats),
        ("baseline_policy", baseline_destroyed, baseline_fp_stats),
    ):
        policy = dict(summary.get(name, {}))
        avoided = policy.get("avoided", {key: 0.0 for key in RANGE_KEYS})
        policy["destroyed"] = destroyed
        policy["net"] = net_range(avoided, destroyed)
        policy["n_false_positives"] = int(stats.get("n_false_positives", 0))
        policy["destroyed_sample"] = {
            k: stats[k]
            for k in (
                "n_sampled",
                "scale_factor",
                "n_with_positive_window",
                "duration_seconds_median",
                "duration_seconds_p90",
                "alert_col",
                "start_col",
            )
            if k in stats
        }
        summary[name] = policy

    model_net = summary["model_policy"]["net"]
    base_net = summary["baseline_policy"]["net"]
    denom_min = abs(base_net["it_kwh_min"]) or 1.0
    denom_max = abs(base_net["it_kwh_max"]) or 1.0
    summary["model_less_bad_than_baseline"] = {
        "it_kwh_ratio_at_pessimistic_net": float(
            abs(base_net["it_kwh_min"]) / abs(model_net["it_kwh_min"])
            if model_net["it_kwh_min"] != 0
            else None
        ),
        "it_kwh_ratio_at_optimistic_net": float(
            abs(base_net["it_kwh_max"]) / abs(model_net["it_kwh_max"])
            if model_net["it_kwh_max"] != 0
            else None
        ),
        "note": (
            "Ratio of |baseline net| / |model net|. "
            f"Pessimistic uses the wide end of each range ({denom_min:.1f} vs "
            "model); both policies remain negative."
        ),
    }
    return summary


def equilibrium_precision(
    avoided: dict[str, float],
    destroyed: dict[str, float],
    *,
    n_costing_tp: int,
    n_fp: int,
) -> dict:
    """Precision vs costing-eligible TPs needed for net energy to break even."""
    if n_costing_tp <= 0 or n_fp <= 0:
        return {"precision": None, "note": "no TPs or FPs"}

    def _p(saved: float, lost: float) -> float | None:
        avg_s = saved / n_costing_tp
        avg_l = lost / n_fp
        denom = avg_s + avg_l
        if denom <= 0:
            return None
        return float(avg_l / denom)

    p_mid = _p(
        0.5 * (avoided["it_kwh_min"] + avoided["it_kwh_max"]),
        0.5 * (destroyed["it_kwh_min"] + destroyed["it_kwh_max"]),
    )
    # Pessimistic: small save, large destroy.
    p_hard = _p(avoided["it_kwh_min"], destroyed["it_kwh_max"])
    p_easy = _p(avoided["it_kwh_max"], destroyed["it_kwh_min"])
    observed = n_costing_tp / (n_costing_tp + n_fp) if (n_costing_tp + n_fp) else None
    return {
        "precision_mid": p_mid,
        "precision_pessimistic": p_hard,
        "precision_optimistic": p_easy,
        "observed_precision_vs_costing_tp": observed,
        "n_costing_tp": int(n_costing_tp),
        "n_fp": int(n_fp),
        "note": (
            "Break-even precision against costing-eligible true positives only "
            "(p * avg_saved > (1-p) * avg_destroyed). Observed precision vs "
            "the 204-row pool is n_costing_TP / (n_costing_TP + n_FP)."
        ),
    }


def sweep_thresholds(
    costing_pool: pd.DataFrame,
    fp_rows: pd.DataFrame,
    *,
    thresholds: tuple[float, ...] = SWEEP_THRESHOLDS,
) -> dict:
    """Reuse one Fan pass (score >= 0.5) and filter by threshold."""
    rows = []
    for threshold in thresholds:
        caught = costing_pool.loc[
            pd.to_numeric(costing_pool["model_score"], errors="coerce") >= threshold
        ]
        avoided = {key: float(caught[key].sum()) if key in caught.columns else 0.0 for key in RANGE_KEYS}
        destroyed = _sum_score_ge(fp_rows, threshold)
        n_tp = int(len(caught))
        if fp_rows.empty or "model_score" not in fp_rows.columns:
            n_fp = 0
        else:
            n_fp = int(
                (pd.to_numeric(fp_rows["model_score"], errors="coerce") >= threshold).sum()
            )
        rows.append(
            {
                "threshold": float(threshold),
                "n_costing_tp_caught": n_tp,
                "n_fp": n_fp,
                "avoided": avoided,
                "destroyed": destroyed,
                "net": net_range(avoided, destroyed),
                "equilibrium": equilibrium_precision(
                    avoided, destroyed, n_costing_tp=max(n_tp, 1) if n_tp else 0, n_fp=n_fp
                ),
            }
        )
    best = min(rows, key=lambda r: r["net"]["it_kwh_min"])
    # "best" = least negative pessimistic net (closest to zero from below, or positive)
    best = max(rows, key=lambda r: r["net"]["it_kwh_min"])
    return {
        "thresholds": list(thresholds),
        "rows": rows,
        "best_threshold_by_pessimistic_net": best["threshold"],
        "best_row": best,
        "note": (
            "Avoided = Role C full-window cost on costing-eligible failures "
            "with model_score >= threshold (kill at admission). Destroyed = "
            "Fan on healthy jobs with score >= 0.5, then filtered."
        ),
    }


def plot_policy_comparison(summary: dict, path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    model = summary["model_policy"]
    baseline = summary["baseline_policy"]
    policies = ("Model t=0.9", "Baseline")
    colors = ("#1f4e79", "#c47b17")

    def _mid(block: dict, lo: str, hi: str) -> float:
        return 0.5 * (float(block[lo]) + float(block[hi]))

    fig, axes = plt.subplots(1, 2, figsize=(10.2, 4.8))

    ax = axes[0]
    avoided = [
        _mid(model["avoided"], "it_kwh_min", "it_kwh_max"),
        _mid(baseline["avoided"], "it_kwh_min", "it_kwh_max"),
    ]
    destroyed = [
        _mid(model["destroyed"], "it_kwh_min", "it_kwh_max"),
        _mid(baseline["destroyed"], "it_kwh_min", "it_kwh_max"),
    ]
    x = np.arange(len(policies))
    width = 0.36
    ax.bar(x - width / 2, avoided, width, color="#2a9d8f", label="Avoided (doomed)", zorder=2)
    ax.bar(x + width / 2, destroyed, width, color="#c44536", label="Destroyed (healthy)", zorder=2)
    ax.set_yscale("log")
    ax.set_xticks(x, policies)
    ax.set_ylabel("IT kWh (log)")
    ax.set_title("Avoided waste vs destroyed useful work")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, axis="y", which="both", alpha=0.3, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax2 = axes[1]
    n_alerts = [
        int(model.get("n_alerts_in_time", 0) or model.get("n_false_positives", 0)),
        int(baseline.get("n_alerts_in_time", 0) or baseline.get("n_false_positives", 0)),
    ]
    # Prefer full test alert counts when present.
    if "n_test_alerts" in model:
        n_alerts[0] = int(model["n_test_alerts"])
    if "n_test_alerts" in baseline:
        n_alerts[1] = int(baseline["n_test_alerts"])
    ax2.bar(x, n_alerts, color=colors, width=0.5, zorder=2)
    ax2.set_yscale("log")
    ax2.set_xticks(x, policies)
    ax2.set_ylabel("Alerts on frozen test (log)")
    ax2.set_title("How often each policy fires")
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    for i, n in enumerate(n_alerts):
        ax2.text(i, n * 1.15, f"{n:,}", ha="center", va="bottom", fontsize=9)
    ax2.grid(True, axis="y", which="both", alpha=0.3, zorder=0)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)

    fig.suptitle(
        "Both policies are net-negative; the model is ~100× less bad\n"
        "because it alerts 0.6% of test rows, not 46%",
        fontsize=11,
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def build_fp_section(
    handoff: pd.DataFrame | None,
    grid: pd.DataFrame | None,
    corners: list[dict[str, float]],
) -> dict:
    napkin = napkin_fp_cost(
        TEST_FALSE_POSITIVES_AT_WORKING_THRESHOLD,
        dt_seconds=TRAIN_SUCCESS_MEDIAN_SECONDS,
        u_mean=PRIMARY_MEAN_CPU_FRAC,
        corners=corners,
    )
    section: dict = {
        "n_test_fp_at_0.9": TEST_FALSE_POSITIVES_AT_WORKING_THRESHOLD,
        "note": (
            "Killing a healthy job destroys useful compute. Both policies are "
            "charged this cost. The napkin is 20,523 model FPs × 10 s × 40.72% CPU."
        ),
        "napkin": {
            "n_fp": TEST_FALSE_POSITIVES_AT_WORKING_THRESHOLD,
            "dt_seconds": TRAIN_SUCCESS_MEDIAN_SECONDS,
            "u_mean": PRIMARY_MEAN_CPU_FRAC,
            "cost": napkin,
        },
    }
    if handoff is not None and grid is not None:
        measured, stats = cost_alerted_successes(handoff, grid, corners)
        section["measured"] = {"cost": measured, **stats}
    return section


def load_success_alerts(
    path: Path,
    *,
    columns: list[str],
    filters: list[tuple] | None = None,
) -> pd.DataFrame:
    return pd.read_parquet(path, columns=columns, filters=filters)


def _attach_alert_counts(summary: dict, handoff: pd.DataFrame) -> None:
    failed = pd.to_numeric(handoff["failed"], errors="coerce").fillna(0).astype(int) == 1
    model_alert = (
        pd.to_numeric(handoff["model_alert"], errors="coerce").fillna(0).astype(int) == 1
    )
    base_alert = (
        pd.to_numeric(handoff["baseline_alert"], errors="coerce").fillna(0).astype(int) == 1
    )
    summary["model_policy"]["n_test_alerts"] = int(model_alert.sum())
    summary["baseline_policy"]["n_test_alerts"] = int(base_alert.sum())
    summary["model_policy"]["n_test_fp"] = int((model_alert & ~failed).sum())
    summary["baseline_policy"]["n_test_fp"] = int((base_alert & ~failed).sum())
    summary["n_test_rows"] = int(len(handoff))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Symmetric avoided-vs-destroyed accounting, threshold sweep, "
            "and the policy figure."
        )
    )
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--sweep", type=Path, default=DEFAULT_SWEEP)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--costing", type=Path, default=DEFAULT_COSTING)
    parser.add_argument("--grid", type=Path, default=DEFAULT_GRID)
    parser.add_argument(
        "--skip-measured-fp",
        action="store_true",
        help="Skip Fan integrals on false positives (figure uses zeros).",
    )
    parser.add_argument("--baseline-sample", type=int, default=BASELINE_SAMPLE_N)
    args = parser.parse_args()

    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    corners = load_physical_cost_params().sweep()

    model_destroyed = {key: 0.0 for key in RANGE_KEYS}
    baseline_destroyed = {key: 0.0 for key in RANGE_KEYS}
    model_stats: dict = {"n_false_positives": TEST_FALSE_POSITIVES_AT_WORKING_THRESHOLD}
    baseline_stats: dict = {}
    sweep_doc: dict | None = None

    if not args.skip_measured_fp and args.handoff.exists() and args.grid.exists():
        LOGGER.info("Loading handoff columns for symmetric FP costing")
        grid = pd.read_parquet(
            args.grid, columns=["machine_id", "time_stamp", "cpu_util_percent"]
        )
        counts = pd.read_parquet(
            args.handoff,
            columns=["failed", "model_alert", "baseline_alert"],
        )
        _attach_alert_counts(summary, counts)
        del counts

        model_fps = load_success_alerts(
            args.handoff,
            columns=[
                "test_row_index",
                "machine_id",
                "decision_time",
                "event_end",
                "failed",
                "model_alert",
                "model_score",
            ],
            filters=[("failed", "=", 0), ("model_score", ">=", SWEEP_MIN_SCORE)],
        )
        LOGGER.info("Model FPs with score >= 0.5: %s", f"{len(model_fps):,}")
        fp_rows, model_ge05_stats = _cost_success_windows(
            model_fps, grid, corners, start_col="decision_time"
        )
        model_destroyed = _sum_score_ge(fp_rows, 0.9)
        n_fp_09 = int(
            (pd.to_numeric(fp_rows["model_score"], errors="coerce") >= 0.9).sum()
        ) if not fp_rows.empty else 0
        model_stats = {
            **model_ge05_stats,
            "n_false_positives": int(summary["model_policy"].get("n_test_fp", n_fp_09)),
            "n_sampled": int(len(model_fps)),
            "scale_factor": 1.0,
            "alert_col": "model_alert",
            "start_col": "decision_time",
            "n_destroyed_windows_at_0.9": n_fp_09,
        }
        summary["false_positives_excluded"] = {
            "n_test_fp_at_0.9": int(summary["model_policy"].get("n_test_fp", n_fp_09)),
            "note": (
                "Destroyed useful work if model-alerted successes are killed "
                "at admission. Not added to the 204-row avoided total."
            ),
            "napkin": {
                "n_fp": TEST_FALSE_POSITIVES_AT_WORKING_THRESHOLD,
                "dt_seconds": TRAIN_SUCCESS_MEDIAN_SECONDS,
                "u_mean": PRIMARY_MEAN_CPU_FRAC,
                "cost": napkin_fp_cost(
                    TEST_FALSE_POSITIVES_AT_WORKING_THRESHOLD,
                    dt_seconds=TRAIN_SUCCESS_MEDIAN_SECONDS,
                    u_mean=PRIMARY_MEAN_CPU_FRAC,
                    corners=corners,
                ),
            },
            "measured": {"cost": model_destroyed, **model_stats},
        }

        baseline_fps = load_success_alerts(
            args.handoff,
            columns=[
                "machine_id",
                "decision_time",
                "event_end",
                "failed",
                "baseline_alert",
                "baseline_alert_time",
            ],
            filters=[("baseline_alert", "=", 1), ("failed", "=", 0)],
        )
        LOGGER.info("Baseline FPs: %s (sampling %s)", f"{len(baseline_fps):,}", args.baseline_sample)
        baseline_destroyed, baseline_stats = cost_alerted_successes(
            baseline_fps,
            grid,
            corners,
            alert_col="baseline_alert",
            start_col="baseline_alert_time",
            sample_n=args.baseline_sample,
            sample_seed=BASELINE_SAMPLE_SEED,
        )

        costing = pd.read_parquet(args.costing)
        scores = pd.read_parquet(
            args.handoff,
            columns=["test_row_index", "model_score", "failed", "eligible_for_costing"],
            filters=[("eligible_for_costing", "=", 1), ("failed", "=", 1)],
        )
        pool = costing.merge(scores[["test_row_index", "model_score"]], on="test_row_index", how="left")
        sweep_doc = sweep_thresholds(pool, fp_rows)
        eq = equilibrium_precision(
            summary["model_policy"]["avoided"],
            model_destroyed,
            n_costing_tp=int(summary.get("n_model_alerts_in_time") or 197),
            n_fp=int(summary["model_policy"].get("n_test_fp") or 20523),
        )
        sweep_doc["official_t_0.9_equilibrium"] = eq

    attach_policy_nets(
        summary,
        model_destroyed=model_destroyed,
        baseline_destroyed=baseline_destroyed,
        model_fp_stats=model_stats,
        baseline_fp_stats=baseline_stats,
    )
    plot_path = plot_policy_comparison(summary, args.figure)
    LOGGER.info("Wrote %s", plot_path)

    args.summary.write_text(
        json.dumps(summary, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    LOGGER.info("Updated %s", args.summary)
    if sweep_doc is not None:
        args.sweep.write_text(
            json.dumps(sweep_doc, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        LOGGER.info("Wrote %s", args.sweep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
