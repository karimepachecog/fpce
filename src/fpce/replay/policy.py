"""Scheduler policy simulation on Role C's costing-eligible test failures.

Does not rescore the 4M-row frozen test and does not retrain Role B.

For each failed, costing-eligible handoff row:
- Do-nothing waste is Role C's full-window range on ``[decision_time, event_end)``.
- Model / baseline savings are Fan integrals on ``[alert_time, event_end)`` when
  the alert is strictly before ``event_end``; otherwise savings are zero.

False positives on healthy jobs are counted in Role B reports and are **not**
converted to kWh. Killing useful work is not avoided waste.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from fpce.config import DATA_PROCESSED, REPORTS_DIR
from fpce.costing.coefficients import load_physical_cost_params
from fpce.costing.translate import translate
from fpce.model.lead_time import WORKING_THRESHOLD

LOGGER = logging.getLogger(__name__)

DEFAULT_HANDOFF = REPORTS_DIR / "role_b_handoff.parquet"
DEFAULT_COSTING = REPORTS_DIR / "role_c_costing.parquet"
DEFAULT_GRID = DATA_PROCESSED / "primary" / "time_grid.parquet"
DEFAULT_PARQUET = REPORTS_DIR / "policy_simulation.parquet"
DEFAULT_SUMMARY = REPORTS_DIR / "policy_simulation.json"

# Documented HistGB t=0.9 test confusion (reports/primary_hgb_thresholds.json).
TEST_FALSE_POSITIVES_AT_WORKING_THRESHOLD = 20523

THRESHOLD_NOTE = (
    "Official operating threshold 0.9 was chosen from test PR/FP behaviour "
    "(reports/primary_hgb_thresholds.json). Ranking metrics (PR-AUC / ROC-AUC) "
    "do not use this threshold. Do not treat 0.9 as a pre-registered cut."
)

RANGE_KEYS = (
    "it_kwh_min",
    "it_kwh_max",
    "facility_kwh_min",
    "facility_kwh_max",
    "water_liters_min",
    "water_liters_max",
)

HANDOFF_COLUMNS = (
    "test_row_index",
    "instance_name",
    "machine_id",
    "decision_time",
    "event_end",
    "failed",
    "eligible_for_costing",
    "model_alert",
    "model_alert_time",
    "baseline_alert",
    "baseline_alert_time",
    "model_score",
    "waste_window_seconds",
)

COSTING_COLUMNS = (
    "test_row_index",
    "it_kwh_min",
    "it_kwh_max",
    "facility_kwh_min",
    "facility_kwh_max",
    "water_liters_min",
    "water_liters_max",
    "u_mean",
    "dt_covered_seconds",
)


def empty_range(*, n_corners: int = 0) -> dict[str, float]:
    return {
        **{key: 0.0 for key in RANGE_KEYS},
        "u_mean": 0.0,
        "dt_covered_seconds": 0.0,
        "n_parameter_corners": int(n_corners),
    }


def sweep_window(
    utilization: np.ndarray,
    dt_seconds: float,
    corners: list[dict[str, float]],
) -> dict[str, float]:
    """Fan + PUE/WUE ranges over one occupancy window."""
    if dt_seconds <= 0 or len(utilization) == 0 or not corners:
        return empty_range(n_corners=len(corners))

    results = [
        translate(utilization_series=utilization, dt_seconds=dt_seconds, params=corner)
        for corner in corners
    ]
    return {
        "it_kwh_min": float(min(r.it_kwh for r in results)),
        "it_kwh_max": float(max(r.it_kwh for r in results)),
        "facility_kwh_min": float(min(r.facility_kwh for r in results)),
        "facility_kwh_max": float(max(r.facility_kwh for r in results)),
        "water_liters_min": float(min(r.water_liters for r in results)),
        "water_liters_max": float(max(r.water_liters for r in results)),
        "u_mean": float(results[0].u_mean),
        "dt_covered_seconds": float(results[0].dt_covered_seconds),
        "n_parameter_corners": len(corners),
    }


def alert_before_end(alert: object, alert_time: object, event_end: object) -> bool:
    """True when a policy fires strictly before the recorded failure time."""
    try:
        flag = int(alert)
    except (TypeError, ValueError):
        return False
    if flag != 1:
        return False
    if pd.isna(alert_time) or pd.isna(event_end):
        return False
    return float(alert_time) < float(event_end)


def filter_costing_failures(handoff: pd.DataFrame) -> pd.DataFrame:
    """Keep failed rows that Role C is allowed to cost."""
    failed = pd.to_numeric(handoff["failed"], errors="coerce").fillna(0).astype(int) == 1
    eligible = (
        pd.to_numeric(handoff["eligible_for_costing"], errors="coerce").fillna(0).astype(int)
        == 1
    )
    return handoff.loc[failed & eligible].copy()


def utilization_fraction(
    machine_grid: pd.DataFrame | None,
    start: float,
    end: float,
) -> np.ndarray:
    """Host CPU in [start, end) as a fraction in [0, 1]."""
    if machine_grid is None or machine_grid.empty:
        return np.array([], dtype=float)
    stamp = pd.to_numeric(machine_grid["time_stamp"], errors="coerce")
    window = machine_grid.loc[(stamp >= start) & (stamp < end)]
    if window.empty:
        return np.array([], dtype=float)
    percent = pd.to_numeric(window["cpu_util_percent"], errors="coerce").fillna(0.0)
    return percent.to_numpy(dtype=float) / 100.0


def _prefix_range(prefix: str, values: dict[str, float]) -> dict[str, float]:
    return {f"{prefix}{key}": float(values[key]) for key in RANGE_KEYS}


def _sum_range(frame: pd.DataFrame, prefix: str) -> dict[str, float]:
    return {
        key: float(pd.to_numeric(frame[f"{prefix}{key}"], errors="coerce").sum())
        for key in RANGE_KEYS
    }


def simulate_policy(
    handoff: pd.DataFrame,
    costing: pd.DataFrame,
    grid: pd.DataFrame,
    corners: list[dict[str, float]],
) -> tuple[pd.DataFrame, dict]:
    """Join Role B alerts with Role C ranges and cost policy tails."""
    eligible = filter_costing_failures(handoff)
    if "test_row_index" not in eligible.columns:
        raise KeyError("Role B handoff is missing test_row_index")
    if "test_row_index" not in costing.columns:
        raise KeyError("Role C costing is missing test_row_index")

    cost_cols = [c for c in COSTING_COLUMNS if c in costing.columns]
    merged = eligible.merge(costing[cost_cols], on="test_row_index", how="left")
    n_missing_c = int(merged["it_kwh_min"].isna().sum()) if "it_kwh_min" in merged.columns else len(merged)

    grid_by_machine = {
        machine_id: group.sort_values("time_stamp")
        for machine_id, group in grid.groupby("machine_id", sort=False)
    }

    rows: list[dict] = []
    for _, row in merged.iterrows():
        decision = float(row["decision_time"])
        event_end = float(row["event_end"])
        machine_id = row["machine_id"]
        machine_grid = grid_by_machine.get(machine_id)

        do_nothing = {
            key: float(row[key]) if key in row.index and pd.notna(row[key]) else 0.0
            for key in RANGE_KEYS
        }

        model_in_time = alert_before_end(
            row.get("model_alert", 0),
            row.get("model_alert_time"),
            event_end,
        )
        baseline_in_time = alert_before_end(
            row.get("baseline_alert", 0),
            row.get("baseline_alert_time"),
            event_end,
        )

        if model_in_time:
            model_start = float(row["model_alert_time"])
            model_saved = sweep_window(
                utilization_fraction(machine_grid, model_start, event_end),
                event_end - model_start,
                corners,
            )
        else:
            model_saved = empty_range(n_corners=len(corners))

        if baseline_in_time:
            baseline_start = float(row["baseline_alert_time"])
            baseline_saved = sweep_window(
                utilization_fraction(machine_grid, baseline_start, event_end),
                event_end - baseline_start,
                corners,
            )
        else:
            baseline_saved = empty_range(n_corners=len(corners))

        record = {
            "test_row_index": int(row["test_row_index"]),
            "instance_name": row.get("instance_name"),
            "machine_id": machine_id,
            "decision_time": decision,
            "event_end": event_end,
            "model_alert": int(model_in_time),
            "baseline_alert": int(baseline_in_time),
            "model_alert_time": (
                float(row["model_alert_time"]) if model_in_time else np.nan
            ),
            "baseline_alert_time": (
                float(row["baseline_alert_time"]) if baseline_in_time else np.nan
            ),
            **_prefix_range("do_nothing_", do_nothing),
            **_prefix_range("model_saved_", model_saved),
            **_prefix_range("baseline_saved_", baseline_saved),
            **{
                f"model_minus_baseline_{key}": float(model_saved[key] - baseline_saved[key])
                for key in RANGE_KEYS
            },
        }
        rows.append(record)

    result = pd.DataFrame(rows)
    summary = build_summary(
        result=result,
        n_eligible=len(eligible),
        n_missing_c=n_missing_c,
        n_corners=len(corners),
    )
    return result, summary


def build_summary(
    result: pd.DataFrame,
    *,
    n_eligible: int,
    n_missing_c: int,
    n_corners: int,
) -> dict:
    if result.empty:
        n_model = 0
        n_baseline = 0
        do_nothing = {key: 0.0 for key in RANGE_KEYS}
        model_saved = {key: 0.0 for key in RANGE_KEYS}
        baseline_saved = {key: 0.0 for key in RANGE_KEYS}
        delta = {key: 0.0 for key in RANGE_KEYS}
    else:
        n_model = int(result["model_alert"].sum())
        n_baseline = int(result["baseline_alert"].sum())
        do_nothing = _sum_range(result, "do_nothing_")
        model_saved = _sum_range(result, "model_saved_")
        baseline_saved = _sum_range(result, "baseline_saved_")
        delta = _sum_range(result, "model_minus_baseline_")

    return {
        "role": "D",
        "status": "policy_simulation",
        "official_model": "HistGradientBoostingClassifier",
        "official_threshold": WORKING_THRESHOLD,
        "threshold_note": THRESHOLD_NOTE,
        "n_costing_eligible_failures": int(n_eligible),
        "n_rows": int(len(result)),
        "n_missing_role_c": int(n_missing_c),
        "n_model_alerts_in_time": n_model,
        "n_baseline_alerts_in_time": n_baseline,
        "n_parameter_corners": int(n_corners),
        "false_positives_excluded": {
            "n_test_fp_at_0.9": TEST_FALSE_POSITIVES_AT_WORKING_THRESHOLD,
            "note": (
                "Role B test false positives at threshold 0.9 are operational "
                "cost (killed healthy work) and are not converted to kWh/liters. "
                "This report only accumulates costing-eligible true failures."
            ),
        },
        "do_nothing": do_nothing,
        "model_policy": {
            "n_alerts_in_time": n_model,
            "avoided": model_saved,
        },
        "baseline_policy": {
            "n_alerts_in_time": n_baseline,
            "avoided": baseline_saved,
        },
        "model_minus_baseline": delta,
        "pool_note": (
            "Lead-time reports in Role B cover 3,778 test failures. This "
            "simulation is the 204-row eligible_for_costing=1 subset only."
        ),
    }


def load_handoff(path: Path) -> pd.DataFrame:
    columns = [c for c in HANDOFF_COLUMNS]
    frame = pd.read_parquet(
        path,
        columns=columns,
        filters=[("eligible_for_costing", "=", 1), ("failed", "=", 1)],
    )
    return frame


def load_costing(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    missing = [c for c in ("test_row_index", *RANGE_KEYS) if c not in frame.columns]
    if missing:
        raise KeyError(f"Role C costing is missing columns: {missing}")
    return frame


def load_grid(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path, columns=["machine_id", "time_stamp", "cpu_util_percent"])


def run_policy_simulation(
    handoff_path: Path = DEFAULT_HANDOFF,
    costing_path: Path = DEFAULT_COSTING,
    grid_path: Path = DEFAULT_GRID,
) -> tuple[pd.DataFrame, dict]:
    if not handoff_path.exists():
        raise FileNotFoundError(f"Role B handoff not found: {handoff_path}")
    if not costing_path.exists():
        raise FileNotFoundError(f"Role C costing not found: {costing_path}")
    if not grid_path.exists():
        raise FileNotFoundError(f"Time grid not found: {grid_path}")

    corners = load_physical_cost_params().sweep()
    LOGGER.info("Loaded %s physical-cost corners", len(corners))
    handoff = load_handoff(handoff_path)
    costing = load_costing(costing_path)
    grid = load_grid(grid_path)
    LOGGER.info(
        "Handoff costing-eligible failures: %s; Role C rows: %s; grid rows: %s",
        f"{len(handoff):,}",
        f"{len(costing):,}",
        f"{len(grid):,}",
    )
    return simulate_policy(handoff, costing, grid, corners)


def write_outputs(
    result: pd.DataFrame,
    summary: dict,
    parquet_path: Path,
    summary_path: Path,
) -> None:
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(parquet_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Accumulate Role C physical-cost ranges on costing-eligible test "
            "failures and compare kill-at-alert (HistGB t=0.9) vs the reactive baseline."
        )
    )
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--costing", type=Path, default=DEFAULT_COSTING)
    parser.add_argument("--grid", type=Path, default=DEFAULT_GRID)
    parser.add_argument("--output", type=Path, default=DEFAULT_PARQUET)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()

    result, summary = run_policy_simulation(
        handoff_path=args.handoff,
        costing_path=args.costing,
        grid_path=args.grid,
    )
    write_outputs(result, summary, args.output, args.summary)

    LOGGER.info(
        "Wrote %s rows to %s and %s",
        len(result),
        args.output,
        args.summary,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
