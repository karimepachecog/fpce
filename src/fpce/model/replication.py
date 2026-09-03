"""Score the frozen HistGB on the replication rack without loading 13M rows at once.

Does not retrain. Uses the primary-rack bundle and the same host join as Role B.
The whole rack is the evaluation set (it was never used for training).
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds

from fpce.config import MODELS_DIR, REPORTS_DIR, RACKS
from fpce.contracts import load_feature_contract
from fpce.costing.coefficients import load_physical_cost_params
from fpce.features.assemble import (
    assemble_feature_frame,
    grid_load_columns,
    instance_load_columns,
    load_host_grid,
    _schema_names,
)
from fpce.model.evaluate import ranking_metrics, threshold_metrics
from fpce.model.freeze import predict_proba_with_bundle
from fpce.model.lead_time import WORKING_THRESHOLD
from fpce.replay.policy import RANGE_KEYS, sweep_window, utilization_fraction

LOGGER = logging.getLogger(__name__)

DEFAULT_EVENTS = Path(RACKS["replication"]["output_dir"]) / "instance_events.parquet"
DEFAULT_GRID = Path(RACKS["replication"]["output_dir"]) / "time_grid.parquet"
DEFAULT_BUNDLE = MODELS_DIR / "primary_hgb_frozen.joblib"
DEFAULT_SUMMARY = REPORTS_DIR / "replication_eval.json"
BATCH_SIZE = 80_000
SCORE_EXTRAS = ("event_end", "eligible_for_costing", "instance_name")


def iter_event_batches(
    path: Path,
    columns: list[str],
    *,
    eligible_col: str = "eligible_for_training",
    batch_size: int = BATCH_SIZE,
):
    scanner = ds.dataset(path, format="parquet").scanner(
        columns=columns,
        filter=pc.field(eligible_col) == 1,
        batch_size=batch_size,
    )
    for batch in scanner.to_batches():
        frame = batch.to_pandas()
        if not frame.empty:
            yield frame


def score_replication(
    events_path: Path,
    grid: pd.DataFrame,
    bundle: dict,
    *,
    batch_size: int = BATCH_SIZE,
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """Return labels, scores, and the costing-eligible slice (with scores)."""
    contract = load_feature_contract()
    columns = instance_load_columns(_schema_names(events_path), contract)
    for extra in SCORE_EXTRAS:
        if extra not in columns:
            names = _schema_names(events_path)
            if extra in names:
                columns.append(extra)

    labels: list[np.ndarray] = []
    scores: list[np.ndarray] = []
    cost_rows: list[pd.DataFrame] = []
    n_seen = 0

    for events in iter_event_batches(events_path, columns, batch_size=batch_size):
        frame = assemble_feature_frame(events, grid, contract)
        proba = predict_proba_with_bundle(bundle, frame)
        y = pd.to_numeric(frame["failed"], errors="coerce").fillna(0).astype(int).to_numpy()
        labels.append(y)
        scores.append(proba.astype(np.float32, copy=False))
        n_seen += len(frame)
        if n_seen % (batch_size * 5) < batch_size:
            LOGGER.info("Scored %s replication rows", f"{n_seen:,}")

        if "eligible_for_costing" in frame.columns:
            mask = pd.to_numeric(frame["eligible_for_costing"], errors="coerce").fillna(0).astype(int) == 1
            if mask.any():
                keep = frame.loc[mask, :].copy()
                keep["model_score"] = proba[mask.to_numpy()]
                keep["model_alert"] = (keep["model_score"] >= float(bundle["threshold"])).astype(int)
                cost_rows.append(
                    keep[
                        [
                            c
                            for c in (
                                "instance_name",
                                "machine_id",
                                "decision_time",
                                "event_end",
                                "failed",
                                "eligible_for_costing",
                                "model_score",
                                "model_alert",
                            )
                            if c in keep.columns
                        ]
                    ]
                )

    y_all = np.concatenate(labels) if labels else np.array([], dtype=int)
    s_all = np.concatenate(scores) if scores else np.array([], dtype=np.float32)
    costing = pd.concat(cost_rows, ignore_index=True) if cost_rows else pd.DataFrame()
    return y_all, s_all, costing


def cost_failures(
    costing: pd.DataFrame,
    grid: pd.DataFrame,
    corners: list[dict[str, float]],
) -> dict:
    if costing.empty:
        return {
            "n_costing_eligible": 0,
            "n_model_alerts": 0,
            "do_nothing": {key: 0.0 for key in RANGE_KEYS},
            "model_avoided": {key: 0.0 for key in RANGE_KEYS},
        }

    failed = costing
    if "failed" in failed.columns:
        failed = failed.loc[pd.to_numeric(failed["failed"], errors="coerce").fillna(0).astype(int) == 1]

    grid_by_machine = {
        machine_id: group.sort_values("time_stamp")
        for machine_id, group in grid.groupby("machine_id", sort=False)
    }

    do_nothing = {key: 0.0 for key in RANGE_KEYS}
    model_avoided = {key: 0.0 for key in RANGE_KEYS}
    n_alerts = 0

    for _, row in failed.iterrows():
        decision = float(row["decision_time"])
        event_end = float(row["event_end"])
        dt = event_end - decision
        machine_grid = grid_by_machine.get(row["machine_id"])
        full = sweep_window(
            utilization_fraction(machine_grid, decision, event_end),
            dt,
            corners,
        )
        for key in RANGE_KEYS:
            do_nothing[key] += float(full[key])
        if int(row.get("model_alert", 0)) == 1 and dt > 0:
            n_alerts += 1
            for key in RANGE_KEYS:
                model_avoided[key] += float(full[key])

    return {
        "n_costing_eligible": int(len(failed)),
        "n_model_alerts": n_alerts,
        "do_nothing": do_nothing,
        "model_avoided": model_avoided,
    }


def load_costing_events(events_path: Path) -> pd.DataFrame:
    """Only the costing-eligible failures (thousands of rows, not millions)."""
    names = _schema_names(events_path)
    columns = [
        c
        for c in (
            "instance_name",
            "machine_id",
            "decision_time",
            "event_end",
            "failed",
            "eligible_for_costing",
        )
        if c in names
    ]
    table = ds.dataset(events_path, format="parquet").to_table(
        columns=columns,
        filter=pc.field("eligible_for_costing") == 1,
    )
    return table.to_pandas()


def try_load_bundle(bundle_path: Path) -> dict | None:
    try:
        return joblib.load(bundle_path)
    except Exception as exc:
        LOGGER.warning("Could not load frozen bundle (%s): %s", bundle_path, exc)
        return None


def run_replication_eval(
    events_path: Path = DEFAULT_EVENTS,
    grid_path: Path = DEFAULT_GRID,
    bundle_path: Path = DEFAULT_BUNDLE,
    *,
    batch_size: int = BATCH_SIZE,
    cost_only: bool = False,
) -> dict:
    if not events_path.exists():
        raise FileNotFoundError(f"Replication events not found: {events_path}")
    if not grid_path.exists():
        raise FileNotFoundError(f"Replication grid not found: {grid_path}")

    contract = load_feature_contract()
    grid = load_host_grid(grid_path, contract)
    LOGGER.info("Replication grid rows: %s", f"{len(grid):,}")
    corners = load_physical_cost_params().sweep()

    bundle = None if cost_only else try_load_bundle(bundle_path)
    if bundle is None:
        costing_events = load_costing_events(events_path)
        costing_events["model_alert"] = 0
        cost_summary = cost_failures(costing_events, grid, corners)
        return {
            "role": "C_replication_costing",
            "rack": "replication",
            "status": "costing_only",
            "classifier_skipped": (
                "Frozen HistGB pickle is sklearn 1.4.2; this environment cannot "
                "unpickle it (Python 3.13 / scikit-learn 1.9). Classification "
                "metrics were not computed. Fan costing does not need the model."
            ),
            "note": (
                "Same 8-day window and hardware class as the primary rack. "
                "This is a replication check, not an out-of-distribution test."
            ),
            "costing": cost_summary,
            "n_parameter_corners": len(corners),
            "primary_test_costing_reference": {
                "n": 204,
                "it_kwh_min": 3.385,
                "it_kwh_max": 9.974,
            },
        }

    y, scores, costing = score_replication(
        events_path, grid, bundle, batch_size=batch_size
    )
    cost_summary = cost_failures(costing, grid, corners)
    threshold = float(bundle.get("threshold", WORKING_THRESHOLD))
    return {
        "role": "B_replication_check",
        "rack": "replication",
        "note": (
            "Same 8-day window and hardware class as the primary rack. "
            "This is a replication check, not an out-of-distribution test. "
            "The official HistGB was not trained on these rows."
        ),
        "official_model": "HistGradientBoostingClassifier",
        "official_threshold": threshold,
        "n_scored": int(len(y)),
        "ranking": ranking_metrics(y, scores),
        "at_working_threshold": threshold_metrics(y, scores, threshold),
        "costing": cost_summary,
        "n_parameter_corners": len(corners),
        "primary_test_reference": {
            "pr_auc": 0.802,
            "roc_auc": 0.984,
            "recall_at_0.9": 0.889,
            "precision_at_0.9": 0.141,
        },
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(
        description="Score the frozen HistGB on the replication rack (batched)."
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--grid", type=Path, default=DEFAULT_GRID)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument(
        "--cost-only",
        action="store_true",
        help="Skip the classifier if the frozen pickle cannot be loaded.",
    )
    args = parser.parse_args()

    summary = run_replication_eval(
        events_path=args.events,
        grid_path=args.grid,
        bundle_path=args.bundle,
        batch_size=args.batch_size,
        cost_only=args.cost_only,
    )
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    LOGGER.info("Wrote %s", args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
