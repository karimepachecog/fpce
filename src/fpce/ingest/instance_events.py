"""Build instance-level prediction events from batch_instance + batch_task.

The modelling unit is a single batch instance. Machine-minute failure is not an
anomaly in this trace (~550 failures/machine over 8 days). Instance-level
Failed/Interrupted is ~0.17% and the counterfactual (kill the doomed instance
at decision_time) is actionable.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from fpce.config import (
    ACTIVE_STATUSES,
    DECISION_OFFSET_SECONDS,
    FAILURE_STATUSES,
    MIN_WASTE_WINDOW_SECONDS,
    RACKS,
    SUCCESS_STATUSES,
)
from fpce.io import write_parquet

TASK_COLS = ["job_name", "task_name", "plan_cpu", "plan_mem", "instance_num"]


def _effective_end(status: pd.Series, start: pd.Series, end: pd.Series) -> pd.Series:
    """Outcome timestamp: recorded end_time, else start_time for failed rows with end=0."""
    start_n = pd.to_numeric(start, errors="coerce").fillna(0)
    end_n = pd.to_numeric(end, errors="coerce").fillna(0)
    is_fail = status.isin(FAILURE_STATUSES)
    is_active = status.isin(ACTIVE_STATUSES)
    event_end = end_n.where(end_n > 0, pd.NA)
    event_end = event_end.where(~(event_end.isna() & is_fail & (start_n > 0)), start_n)
    # Censored (still running): no usable end; leave NA.
    event_end = event_end.where(~(event_end.isna() & is_active), pd.NA)
    return event_end


def build_instance_events(
    instances: pd.DataFrame,
    tasks: pd.DataFrame | None = None,
    decision_offset: int = DECISION_OFFSET_SECONDS,
    min_waste_window: int = MIN_WASTE_WINDOW_SECONDS,
) -> pd.DataFrame:
    """Return one row per batch instance with label, decision time, and waste window."""
    out = instances.copy()
    if tasks is not None and not tasks.empty:
        task_keys = tasks[TASK_COLS].drop_duplicates(subset=["job_name", "task_name"])
        out = out.merge(task_keys, on=["job_name", "task_name"], how="left")
    else:
        for col in ("plan_cpu", "plan_mem", "instance_num"):
            if col not in out.columns:
                out[col] = pd.NA

    out["failed"] = out["status"].isin(FAILURE_STATUSES).astype("int8")
    out["outcome"] = "other"
    out.loc[out["status"].isin(FAILURE_STATUSES), "outcome"] = "failed"
    out.loc[out["status"].isin(SUCCESS_STATUSES), "outcome"] = "succeeded"
    out.loc[out["status"].isin(ACTIVE_STATUSES), "outcome"] = "censored"

    start = pd.to_numeric(out["start_time"], errors="coerce").fillna(0)
    out["decision_time"] = (start + decision_offset).astype("int64")
    out["event_end"] = _effective_end(out["status"], out["start_time"], out["end_time"])
    waste = pd.to_numeric(out["event_end"], errors="coerce") - out["decision_time"]
    out["waste_window_seconds"] = waste.clip(lower=0)

    out["eligible_for_training"] = out["outcome"].isin(["failed", "succeeded"]).astype("int8")
    out["eligible_for_costing"] = (
        (out["outcome"] == "failed")
        & out["waste_window_seconds"].notna()
        & (out["waste_window_seconds"] >= min_waste_window)
    ).astype("int8")

    keep = [
        "instance_name",
        "task_name",
        "job_name",
        "task_type",
        "machine_id",
        "seq_no",
        "total_seq_no",
        "plan_cpu",
        "plan_mem",
        "instance_num",
        "start_time",
        "end_time",
        "status",
        "cpu_avg",
        "cpu_max",
        "mem_avg",
        "mem_max",
        "failed",
        "outcome",
        "decision_time",
        "event_end",
        "waste_window_seconds",
        "eligible_for_training",
        "eligible_for_costing",
    ]
    keep = [c for c in keep if c in out.columns]
    return out[keep]


def build_rack_instance_events(rack: str) -> pd.DataFrame:
    output_dir = Path(RACKS[rack]["output_dir"])
    instances = pd.read_parquet(output_dir / "batch_instance.parquet")
    tasks_path = output_dir / "batch_task.parquet"
    tasks = pd.read_parquet(tasks_path) if tasks_path.exists() else None
    return build_instance_events(instances, tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build instance-level prediction events")
    parser.add_argument("--rack", choices=list(RACKS.keys()), default="primary")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    events = build_rack_instance_events(args.rack)
    out_path = args.output or (Path(RACKS[args.rack]["output_dir"]) / "instance_events.parquet")
    write_parquet(events, out_path)

    n = len(events)
    n_train = int(events["eligible_for_training"].sum())
    n_fail = int(events["failed"].sum())
    rate = (events.loc[events["eligible_for_training"] == 1, "failed"].mean() * 100) if n_train else 0.0
    n_cost = int(events["eligible_for_costing"].sum())
    print(
        f"[ok] instance_events ({args.rack}): {n:,} rows -> {out_path} "
        f"(training={n_train:,} failed={n_fail:,} rate={rate:.4f}% costing={n_cost:,})"
    )


if __name__ == "__main__":
    main()
