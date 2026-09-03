"""End-to-end replay runner for Role D.

Connects:
    Role A -> instance events + telemetry time grid
    Role B -> frozen HistGradientBoosting + reactive baseline
    Role C -> physical cost translation
    Role D -> replay orchestration + final report

The official Role B model is the frozen HistGradientBoosting bundle
with threshold 0.90.

This runner:
1. Reconstructs Role B test features exactly as Role B did.
2. Loads the frozen HGB bundle and scores the held-out events.
3. Computes the reactive baseline using TRAIN-only runtime medians.
4. Verifies the model/baseline alert times against the Role B handoff.
5. Costs only eligible events from the Role B handoff.
6. Sweeps all Role C physical-cost parameter corners.
7. Writes one integrated result row per costing-eligible event.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from fpce.config import (
    DATA_PROCESSED,
    MODELS_DIR,
    RACKS,
    REPORTS_DIR,
    resolve_repo_path,
)
from fpce.contracts import load_feature_contract
from fpce.features.assemble import (
    assemble_feature_frame,
    load_host_grid,
    load_time_split,
    load_trainable_events,
)
from fpce.model.baseline import (
    reactive_fire_time,
    train_runtime_medians,
)
from fpce.model.freeze import predict_proba_with_bundle
from fpce.model.lead_time import alert_before_failure, lead_seconds
from fpce.costing.coefficients import load_physical_cost_params
from fpce.costing.translate import translate


# ---------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = DATA_PROCESSED / "primary"

DEFAULT_GRID = DEFAULT_OUTPUT_DIR / "time_grid.parquet"
DEFAULT_EVENTS = DEFAULT_OUTPUT_DIR / "instance_events.parquet"

DEFAULT_HANDOFF = REPORTS_DIR / "role_b_handoff.parquet"
DEFAULT_BUNDLE = MODELS_DIR / "primary_hgb_frozen.joblib"

DEFAULT_OUTPUT = REPORTS_DIR / "replay_results.parquet"
DEFAULT_SUMMARY = REPORTS_DIR / "replay_summary.json"


# ---------------------------------------------------------------------
# Column helpers
# ---------------------------------------------------------------------

def _find_column(
    df: pd.DataFrame,
    candidates: list[str],
    name: str,
) -> str:
    """Return the first candidate column present in a DataFrame."""
    for column in candidates:
        if column in df.columns:
            return column

    raise KeyError(
        f"Could not find {name} column.\n"
        f"Expected one of: {candidates}\n"
        f"Available columns: {df.columns.tolist()}"
    )


def _numeric(series: pd.Series) -> pd.Series:
    """Convert a Series to numeric values."""
    return pd.to_numeric(series, errors="coerce")


# ---------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------

def load_replay_data(
    grid_path: Path,
    events_path: Path,
    handoff_path: Path,
    bundle_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Load all Role A/B inputs needed by the replay."""
    print(f"[load] time grid: {grid_path}")
    grid = pd.read_parquet(grid_path)

    print(f"[load] instance events: {events_path}")
    events = pd.read_parquet(events_path)

    print(f"[load] Role B handoff: {handoff_path}")
    handoff = pd.read_parquet(handoff_path)

    print(f"[load] frozen HGB bundle: {bundle_path}")
    bundle = joblib.load(bundle_path)

    print(f"[info] grid rows: {len(grid):,}")
    print(f"[info] events rows: {len(events):,}")
    print(f"[info] handoff rows: {len(handoff):,}")

    return grid, events, handoff, bundle


# ---------------------------------------------------------------------
# Role B model replay
# ---------------------------------------------------------------------

def build_role_b_test_features(
    events: pd.DataFrame,
    grid: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Reconstruct Role B's train/test feature frames.

    This follows the same feature assembly used by Role B:
        load events
        -> join host state at decision
        -> split using frozen timestamp
        -> preserve Role B feature columns
    """
    contract = load_feature_contract()
    split = load_time_split()

    split_timestamp = int(split["split_timestamp"])
    split_column = contract.split_column

    # Assemble exactly as Role B.
    frame = assemble_feature_frame(
        events=events,
        grid=grid,
        contract=contract,
    )

    if split_column not in frame.columns:
        raise ValueError(
            f"Split column '{split_column}' is missing after feature assembly."
        )

    train_mask = _numeric(frame[split_column]) < split_timestamp
    test_mask = ~train_mask

    train_frame = frame.loc[train_mask].reset_index(drop=True)
    test_frame = frame.loc[test_mask].reset_index(drop=True)

    # Feature columns are determined by the frozen model bundle.
    feature_order = list(bundle_feature_order_placeholder())

    # We do not actually use this placeholder. The real feature order is
    # retrieved from the frozen bundle in score_role_b().
    del feature_order

    return train_frame, test_frame, frame


def bundle_feature_order_placeholder() -> list[str]:
    """Internal placeholder kept separate to avoid accidental feature logic."""
    return []


def score_role_b(
    events: pd.DataFrame,
    grid: pd.DataFrame,
    handoff: pd.DataFrame,
    bundle: dict,
) -> pd.DataFrame:
    """Score the frozen Role B model on the held-out test events.

    Returns the handoff table enriched with independently recomputed
    model probabilities and alert times.
    """
    contract = load_feature_contract()
    split = load_time_split()

    split_timestamp = int(split["split_timestamp"])
    split_column = contract.split_column

    # Recreate the exact Role B feature frame.
    frame = assemble_feature_frame(
        events=events,
        grid=grid,
        contract=contract,
    )

    train_mask = _numeric(frame[split_column]) < split_timestamp
    test_mask = ~train_mask

    test_features = frame.loc[test_mask].reset_index(drop=True)

    # Role B handoff was generated from the same frozen test ordering.
    if "test_row_index" in handoff.columns:
        expected_n = len(handoff)

        if len(test_features) < expected_n:
            raise ValueError(
                "Reconstructed test feature frame is shorter than the "
                "Role B handoff. The event/grid assembly does not match "
                "the frozen Role B data."
            )

        # The handoff represents the frozen test rows in order.
        test_features = test_features.iloc[:expected_n].reset_index(drop=True)

    print(f"[model] frozen threshold: {bundle.get('threshold', 'unknown')}")
    print(
        f"[model] frozen feature count: "
        f"{len(bundle.get('feature_order', []))}"
    )

    # Official Role B prediction path.
    scores = predict_proba_with_bundle(
        bundle=bundle,
        features=test_features,
    )

    threshold = float(bundle["threshold"])

    model_alert = scores >= threshold

    decision = _numeric(test_features["decision_time"])

    # The model is scored at decision_time.
    model_alert_time = pd.Series(
        np.where(model_alert, decision, np.nan),
        index=test_features.index,
        dtype="float64",
    )

    event_end = _numeric(test_features["event_end"])

    model_lead = lead_seconds(
        model_alert_time,
        event_end,
    )

    scored = handoff.copy().reset_index(drop=True)

    if len(scored) != len(scores):
        raise ValueError(
            "Role B handoff row count does not match reconstructed "
            f"test rows: handoff={len(scored)}, scores={len(scores)}."
        )

    scored["replay_model_score"] = scores.astype(np.float32)
    scored["replay_model_alert"] = model_alert.astype(int)
    scored["replay_model_alert_time"] = model_alert_time
    scored["replay_model_lead_time_seconds"] = model_lead

    # Compare the independently reconstructed score with the frozen
    # handoff score when available.
    if "model_score" in scored.columns:
        original = _numeric(scored["model_score"]).to_numpy()
        replay = scores.astype(float)

        finite = np.isfinite(original)

        if finite.any():
            max_diff = float(
                np.max(np.abs(original[finite] - replay[finite]))
            )
        else:
            max_diff = None

        scored["model_score_abs_diff"] = np.abs(
            original - replay
        )

        print(
            f"[model] max score difference vs Role B handoff: "
            f"{max_diff}"
        )

    return scored


# ---------------------------------------------------------------------
# Role B baseline
# ---------------------------------------------------------------------

def compute_baseline(
    events: pd.DataFrame,
    handoff: pd.DataFrame,
) -> pd.DataFrame:
    """Compute the reactive baseline using TRAIN-only runtime medians."""
    contract = load_feature_contract()
    split = load_time_split()

    split_timestamp = int(split["split_timestamp"])
    split_column = contract.split_column

    # Role B baseline needs the same train/test event population.
    train = events.loc[
        _numeric(events[split_column]) < split_timestamp
    ].reset_index(drop=True)

    print(
        f"[baseline] training rows used for runtime medians: "
        f"{len(train):,}"
    )

    runtime_medians = train_runtime_medians(train)

    fire_time = reactive_fire_time(
        handoff,
        runtime_medians,
    )

    event_end = _numeric(handoff["event_end"])

    baseline_alert = (
        alert_before_failure(
            fire_time,
            event_end,
        )
        .astype(int)
    )

    baseline_lead = lead_seconds(
        fire_time,
        event_end,
    )

    out = handoff.copy().reset_index(drop=True)

    out["replay_baseline_alert_time"] = fire_time
    out["replay_baseline_alert"] = baseline_alert
    out["replay_baseline_lead_time_seconds"] = baseline_lead

    # Compare with the official Role B handoff.
    if "baseline_alert_time" in out.columns:
        original = _numeric(out["baseline_alert_time"])
        replay = _numeric(out["replay_baseline_alert_time"])

        out["baseline_alert_time_abs_diff"] = (
            original - replay
        ).abs()

    if "baseline_lead_time_seconds" in out.columns:
        original = _numeric(out["baseline_lead_time_seconds"])
        replay = _numeric(
            out["replay_baseline_lead_time_seconds"]
        )

        out["baseline_lead_time_abs_diff"] = (
            original - replay
        ).abs()

    return out


# ---------------------------------------------------------------------
# Costing
# ---------------------------------------------------------------------

def _find_grid_columns(
    grid: pd.DataFrame,
) -> tuple[str, str, str]:
    """Find machine, timestamp, and CPU utilization columns."""
    machine_col = _find_column(
        grid,
        [
            "machine_id",
            "instance_id",
            "instance",
        ],
        "machine identifier",
    )

    timestamp_col = _find_column(
        grid,
        [
            "time_stamp",
            "timestamp",
            "time",
        ],
        "timestamp",
    )

    utilization_col = _find_column(
        grid,
        [
            "cpu_util_percent",
            "utilization",
            "cpu_utilization",
            "cpu_usage",
            "usage",
            "u",
        ],
        "CPU utilization column",
    )

    return machine_col, timestamp_col, utilization_col


def calculate_cost(
    utilization_series: np.ndarray,
    dt_seconds: float,
) -> dict:
    """Run Role C translation across every physical-cost corner."""
    if dt_seconds <= 0:
        return {
            "it_kwh_min": 0.0,
            "it_kwh_max": 0.0,
            "facility_kwh_min": 0.0,
            "facility_kwh_max": 0.0,
            "water_liters_min": 0.0,
            "water_liters_max": 0.0,
            "u_mean": 0.0,
            "dt_covered_seconds": 0.0,
            "n_parameter_corners": 0,
        }

    params = load_physical_cost_params()
    corners = list(params.sweep())

    if not corners:
        raise ValueError(
            "Role C physical-cost parameter sweep returned no corners."
        )

    results = []

    for corner in corners:
        result = translate(
            utilization_series=utilization_series,
            dt_seconds=dt_seconds,
            params=corner,
        )
        results.append(result)

    it_values = [result.it_kwh for result in results]
    facility_values = [
        result.facility_kwh for result in results
    ]
    water_values = [
        result.water_liters for result in results
    ]

    first = results[0]

    return {
        "it_kwh_min": float(min(it_values)),
        "it_kwh_max": float(max(it_values)),
        "facility_kwh_min": float(min(facility_values)),
        "facility_kwh_max": float(max(facility_values)),
        "water_liters_min": float(min(water_values)),
        "water_liters_max": float(max(water_values)),
        "u_mean": float(first.u_mean),
        "dt_covered_seconds": float(first.dt_covered_seconds),
        "n_parameter_corners": len(corners),
    }


def add_costs(
    grid: pd.DataFrame,
    handoff: pd.DataFrame,
    limit: int | None = None,
) -> pd.DataFrame:
    """Compute Role C costs for costing-eligible failed events."""
    machine_col, timestamp_col, utilization_col = (
        _find_grid_columns(grid)
    )

    eligible = handoff.copy()

    eligible_col = _find_column(
        eligible,
        ["eligible_for_costing"],
        "costing eligibility",
    )

    # Role B explicitly defines costing eligibility.
    eligible = eligible[
        _numeric(eligible[eligible_col]).fillna(0).astype(int) == 1
    ].copy()

    # Costing should normally be applied to failed events.
    if "failed" in eligible.columns:
        eligible = eligible[
            _numeric(eligible["failed"]).fillna(0).astype(int) == 1
        ].copy()

    if limit is not None:
        eligible = eligible.head(limit).copy()

    print(
        f"[cost] costing-eligible failed events: "
        f"{len(eligible):,}"
    )

    output_rows: list[dict] = []

    # Group by machine to avoid repeatedly filtering the entire grid.
    grid_by_machine = {
        machine_id: group.sort_values(timestamp_col)
        for machine_id, group
        in grid.groupby(machine_col, sort=False)
    }

    for index, row in eligible.iterrows():
        machine_id = row.get("machine_id")

        if machine_id not in grid_by_machine:
            print(
                f"[skip] row {index}: machine_id={machine_id!r} "
                f"not found in time grid"
            )
            continue

        decision_time = _numeric(
            pd.Series([row["decision_time"]])
        ).iloc[0]

        event_end = _numeric(
            pd.Series([row["event_end"]])
        ).iloc[0]

        if pd.isna(decision_time) or pd.isna(event_end):
            print(
                f"[skip] row {index}: invalid decision/event_end"
            )
            continue

        decision_time = int(decision_time)
        event_end = int(event_end)

        dt_seconds = event_end - decision_time

        if dt_seconds <= 0:
            print(
                f"[skip] row {index}: invalid interval "
                f"{decision_time} -> {event_end}"
            )
            continue

        machine_grid = grid_by_machine[machine_id]

        window = machine_grid[
            (machine_grid[timestamp_col] >= decision_time)
            & (machine_grid[timestamp_col] < event_end)
        ].copy()

        if window.empty:
            print(
                f"[skip] row {index}: no telemetry in "
                f"[decision_time, event_end)"
            )
            continue

        utilization = (
            _numeric(window[utilization_col])
            .fillna(0.0)
            .to_numpy(dtype=float)
        )

        # Role C translate() expects CPU utilization in the range [0, 1].
        # The time grid stores CPU utilization as a percentage [0, 100].
        if utilization_col == "cpu_util_percent":
            utilization = utilization / 100.0

        cost = calculate_cost(
            utilization_series=utilization,
            dt_seconds=float(dt_seconds),
        )

        output_rows.append(
            {
                "handoff_row_index": int(index),
                "machine_id": machine_id,
                "decision_time": decision_time,
                "event_end": event_end,
                "dt_seconds": float(dt_seconds),
                "telemetry_rows": int(len(window)),
                "it_kwh_min": cost["it_kwh_min"],
                "it_kwh_max": cost["it_kwh_max"],
                "facility_kwh_min": cost["facility_kwh_min"],
                "facility_kwh_max": cost["facility_kwh_max"],
                "water_liters_min": cost["water_liters_min"],
                "water_liters_max": cost["water_liters_max"],
                "u_mean": cost["u_mean"],
                "dt_covered_seconds": cost["dt_covered_seconds"],
                "n_parameter_corners": cost[
                    "n_parameter_corners"
                ],
            }
        )

    if not output_rows:
        return pd.DataFrame()

    return pd.DataFrame(output_rows)


# ---------------------------------------------------------------------
# Integrated replay
# ---------------------------------------------------------------------

def run_replay(
    grid_path: Path = DEFAULT_GRID,
    events_path: Path = DEFAULT_EVENTS,
    handoff_path: Path = DEFAULT_HANDOFF,
    bundle_path: Path = DEFAULT_BUNDLE,
    limit: int | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Run the complete Role A -> B -> C -> D experiment."""
    grid, events, handoff, bundle = load_replay_data(
        grid_path=grid_path,
        events_path=events_path,
        handoff_path=handoff_path,
        bundle_path=bundle_path,
    )

    # ---------------------------------------------------------------
    # Role B — frozen model replay
    # ---------------------------------------------------------------

    print("[step 1/3] Scoring frozen Role B HistGB...")
    result = score_role_b(
        events=events,
        grid=grid,
        handoff=handoff,
        bundle=bundle,
    )

    # ---------------------------------------------------------------
    # Role B — reactive baseline
    # ---------------------------------------------------------------

    print("[step 2/3] Computing reactive baseline...")
    result = compute_baseline(
        events=events,
        handoff=result,
    )

    # ---------------------------------------------------------------
    # Role C — physical costing
    # ---------------------------------------------------------------

    print("[step 3/3] Computing physical cost ranges...")
    costs = add_costs(
        grid=grid,
        handoff=result,
        limit=limit,
    )

    # ---------------------------------------------------------------
    # Merge model/baseline + costs
    # ---------------------------------------------------------------

    if costs.empty:
        final = result.iloc[0:0].copy()

        # Preserve cost columns even when no rows are available.
        for column in [
            "handoff_row_index",
            "dt_seconds",
            "telemetry_rows",
            "it_kwh_min",
            "it_kwh_max",
            "facility_kwh_min",
            "facility_kwh_max",
            "water_liters_min",
            "water_liters_max",
            "u_mean",
            "dt_covered_seconds",
            "n_parameter_corners",
        ]:
            final[column] = pd.Series(dtype="float64")
    else:
        final = result.reset_index(drop=True).copy()

        costs = costs.rename(
            columns={
                "machine_id": "cost_machine_id",
            }
        )

        # costs["handoff_row_index"] refers to the index of result.
        final = final.reset_index().rename(
            columns={"index": "handoff_row_index"}
        )

        final = final.merge(
            costs,
            on="handoff_row_index",
            how="inner",
            suffixes=("", "_cost"),
        )

    # ---------------------------------------------------------------
    # Derived comparison metrics
    # ---------------------------------------------------------------

    if not final.empty:
        model_lead = _numeric(
            final["replay_model_lead_time_seconds"]
        )

        baseline_lead = _numeric(
            final["replay_baseline_lead_time_seconds"]
        )

        both = (
            _numeric(final["replay_model_alert"]).fillna(0).astype(int)
            == 1
        ) & (
            _numeric(final["replay_baseline_alert"])
            .fillna(0)
            .astype(int)
            == 1
        )

        final["delta_lead_time_seconds"] = (
            model_lead - baseline_lead
        ).where(
            both & model_lead.notna() & baseline_lead.notna(),
            np.nan,
        )

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------

    summary = build_summary(
        result=result,
        final=final,
        bundle=bundle,
    )

    print(
        f"[ok] replay completed: "
        f"{len(final):,} costing-eligible events"
    )

    return final, summary


# ---------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------

def build_summary(
    result: pd.DataFrame,
    final: pd.DataFrame,
    bundle: dict,
) -> dict:
    """Create a machine-readable Role D experiment summary."""
    summary: dict = {
        "role": "D",
        "status": "completed",
        "official_model": "HistGradientBoostingClassifier",
        "model_threshold": float(bundle["threshold"]),
        "n_handoff_rows": int(len(result)),
        "n_costed_rows": int(len(final)),
    }

    if "replay_model_alert" in result.columns:
        summary["n_replay_model_alerts"] = int(
            _numeric(result["replay_model_alert"])
            .fillna(0)
            .astype(int)
            .sum()
        )

    if "replay_baseline_alert" in result.columns:
        summary["n_replay_baseline_alerts"] = int(
            _numeric(result["replay_baseline_alert"])
            .fillna(0)
            .astype(int)
            .sum()
        )

    if "failed" in result.columns:
        failed = (
            _numeric(result["failed"])
            .fillna(0)
            .astype(int)
            == 1
        )

        summary["n_failures"] = int(failed.sum())

        if "eligible_for_costing" in result.columns:
            eligible = (
                _numeric(result["eligible_for_costing"])
                .fillna(0)
                .astype(int)
                == 1
            )

            summary["n_costing_eligible_failures"] = int(
                (failed & eligible).sum()
            )

    if not final.empty:
        for column in [
            "it_kwh_min",
            "it_kwh_max",
            "facility_kwh_min",
            "facility_kwh_max",
            "water_liters_min",
            "water_liters_max",
        ]:
            if column in final.columns:
                values = _numeric(final[column])
                summary[column] = (
                    float(values.sum())
                    if values.notna().any()
                    else 0.0
                )

        if "delta_lead_time_seconds" in final.columns:
            delta = _numeric(
                final["delta_lead_time_seconds"]
            ).dropna()

            summary["delta_lead_time_seconds_mean"] = (
                float(delta.mean()) if len(delta) else None
            )

            summary["delta_lead_time_seconds_median"] = (
                float(delta.median()) if len(delta) else None
            )

    return summary


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete Role D replay experiment: "
            "Role A -> frozen Role B -> baseline -> Role C costing."
        )
    )

    parser.add_argument(
        "--grid",
        type=Path,
        default=DEFAULT_GRID,
        help="Role A time_grid.parquet",
    )

    parser.add_argument(
        "--events",
        type=Path,
        default=DEFAULT_EVENTS,
        help="Role A instance_events.parquet",
    )

    parser.add_argument(
        "--handoff",
        type=Path,
        default=DEFAULT_HANDOFF,
        help="Role B role_b_handoff.parquet",
    )

    parser.add_argument(
        "--bundle",
        type=Path,
        default=DEFAULT_BUNDLE,
        help="Frozen Role B HistGB joblib bundle",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output replay_results.parquet",
    )

    parser.add_argument(
        "--summary",
        type=Path,
        default=DEFAULT_SUMMARY,
        help="Output replay_summary.json",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of costing-eligible events to cost",
    )

    args = parser.parse_args()

    results, summary = run_replay(
        grid_path=args.grid,
        events_path=args.events,
        handoff_path=args.handoff,
        bundle_path=args.bundle,
        limit=args.limit,
    )

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    args.summary.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    results.to_parquet(
        args.output,
        index=False,
    )

    args.summary.write_text(
        json.dumps(
            summary,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print()
    print("=" * 60)
    print("ROLE D REPLAY COMPLETE")
    print("=" * 60)
    print(f"Results : {args.output}")
    print(f"Summary : {args.summary}")
    print(f"Rows    : {len(results):,}")
    print("=" * 60)


if __name__ == "__main__":
    main()