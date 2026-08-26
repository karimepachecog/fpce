"""Join host time-grid state at instance decision time (no future rows)."""

from __future__ import annotations

import pandas as pd

from fpce.contracts import load_feature_contract


def join_host_at_decision(
    events: pd.DataFrame,
    grid: pd.DataFrame,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Attach the latest host-grid row with time_stamp <= decision_time.

    Uses pandas merge_asof(..., direction="backward") keyed by machine_id.
    Raises if any attached grid timestamp is strictly after decision_time.
    """
    if "decision_time" not in events.columns:
        raise ValueError("events must include decision_time")
    if "machine_id" not in events.columns or "machine_id" not in grid.columns:
        raise ValueError("events and grid must include machine_id")
    if "time_stamp" not in grid.columns:
        raise ValueError("grid must include time_stamp")

    contract = load_feature_contract()
    if columns is None:
        columns = [c for c in contract.allow_from_time_grid if c in grid.columns]
    missing = [c for c in columns if c not in grid.columns]
    if missing:
        raise ValueError(f"grid missing columns: {missing}")

    keep_grid = ["machine_id", "time_stamp", *columns]
    right = grid.loc[:, keep_grid].copy()
    right["time_stamp"] = pd.to_numeric(right["time_stamp"], errors="coerce")
    left = events.copy()
    left["decision_time"] = pd.to_numeric(left["decision_time"], errors="coerce")
    left["_row_id"] = range(len(left))
    left = left.sort_values(["machine_id", "decision_time", "_row_id"])
    right = right.sort_values(["machine_id", "time_stamp"])

    joined = pd.merge_asof(
        left,
        right,
        left_on="decision_time",
        right_on="time_stamp",
        by="machine_id",
        direction="backward",
        suffixes=("", "_grid"),
    )
    future = joined["time_stamp"].notna() & (
        joined["time_stamp"] > joined["decision_time"]
    )
    if bool(future.any()):
        raise ValueError(
            f"host join leaked {int(future.sum())} future grid rows "
            "(time_stamp > decision_time)"
        )
    return joined.sort_values("_row_id").drop(columns="_row_id").reset_index(drop=True)
