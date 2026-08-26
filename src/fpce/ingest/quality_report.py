"""Generate data quality report for processed trace tables."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from fpce.config import REPORTS_DIR, racks_of_kind
from fpce.ingest.instance_events import COSTING_WINDOW_THRESHOLDS, costing_pool_by_threshold


def pct_null(df: pd.DataFrame) -> dict[str, float]:
    return {col: round(float(df[col].isna().mean() * 100), 2) for col in df.columns}


def build_report(rack_name: str) -> dict:
    racks = racks_of_kind("alibaba")
    rack_cfg = racks[rack_name]
    output_dir = Path(rack_cfg["output_dir"])

    usage = pd.read_parquet(output_dir / "machine_usage.parquet")
    instances = pd.read_parquet(output_dir / "batch_instance.parquet")
    tasks = pd.read_parquet(output_dir / "batch_task.parquet")
    grid = pd.read_parquet(output_dir / "time_grid.parquet")

    report = {
        "rack": rack_name,
        "label": rack_cfg["label"],
        "machine_usage_rows": len(usage),
        "batch_instance_rows": len(instances),
        "batch_task_rows": len(tasks),
        "time_grid_rows": len(grid),
        "machines": int(usage["machine_id"].nunique()),
        "time_range_seconds": {
            "min": int(usage["time_stamp"].min()),
            "max": int(usage["time_stamp"].max()),
        },
        "null_pct": {
            "machine_usage": pct_null(usage),
            "batch_instance": pct_null(instances),
            "time_grid": pct_null(grid),
        },
        "cpu_util_percent": {
            "mean": round(float(usage["cpu_util_percent"].mean()), 2),
            "p50": round(float(usage["cpu_util_percent"].median()), 2),
            "p95": round(float(usage["cpu_util_percent"].quantile(0.95)), 2),
        },
        "failure_status_counts": instances["status"].value_counts().to_dict(),
        "failure_within_horizon_rate": round(
            float(grid["failure_within_horizon"].mean() * 100), 4
        ),
        "data_gap_rate_pct": round(float(grid["data_gap"].mean() * 100), 2),
        "note": (
            "failure_within_horizon is an auxiliary machine-minute column, not the "
            "prediction target. See instance_events."
        ),
    }

    events_path = output_dir / "instance_events.parquet"
    if events_path.exists():
        events = pd.read_parquet(events_path)
        trainable = events["eligible_for_training"] == 1
        report["instance_events"] = {
            "rows": len(events),
            "failed_rows": int(events["failed"].sum()),
            "trainable_rows": int(trainable.sum()),
            "trainable_positive_rate_pct": round(
                float(events.loc[trainable, "failed"].mean() * 100), 4
            ) if trainable.any() else None,
            "costing_rows": int(events["eligible_for_costing"].sum()),
            "censored_rows": int((events["outcome"] == "censored").sum()),
            "median_waste_window_seconds": (
                None
                if events["waste_window_seconds"].dropna().empty
                else round(float(events["waste_window_seconds"].median()), 2)
            ),
            "costing_pool_by_threshold_seconds": costing_pool_by_threshold(
                events, COSTING_WINDOW_THRESHOLDS
            ),
            "imputed_upper_bound_rows": (
                int(events["waste_window_imputed"].sum())
                if "waste_window_imputed" in events.columns
                else 0
            ),
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate data quality report")
    parser.add_argument(
        "--rack",
        choices=list(racks_of_kind("alibaba")),
        action="append",
        default=None,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPORTS_DIR / "data_quality.json",
    )
    args = parser.parse_args()

    rack_names = args.rack or list(racks_of_kind("alibaba"))
    report = {
        "racks": {name: build_report(name) for name in rack_names},
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[ok] Quality report -> {args.output}")


if __name__ == "__main__":
    main()
