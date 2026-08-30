"""Reactive baseline for lead-time comparison (Role B contract).

Fires at the earlier of:
- retry: ``seq_no >= 2`` known at admission → ``decision_time``
- runtime: ``decision_time + median(duration | succeeded, task_type)`` from TRAIN only

A fire at or after ``event_end`` does not count (no hindsight after the failure).
"""

from __future__ import annotations

import pandas as pd

RETRY_SEQ_NO_MIN = 2


def train_runtime_medians(
    train: pd.DataFrame,
    *,
    group_col: str = "task_type",
    duration_col: str = "waste_window_seconds",
    label_col: str = "failed",
) -> pd.Series:
    """Median successful duration by task_type, plus a global fallback."""
    duration = pd.to_numeric(train[duration_col], errors="coerce")
    succeeded = train[label_col].to_numpy() == 0
    grouped = duration[succeeded].groupby(train.loc[succeeded, group_col], dropna=False)
    medians = grouped.median()
    global_median = float(duration[succeeded].median()) if succeeded.any() else 0.0
    medians.attrs["global_median"] = global_median
    return medians


def reactive_fire_time(
    events: pd.DataFrame,
    runtime_medians: pd.Series,
    *,
    seq_col: str = "seq_no",
    group_col: str = "task_type",
    decision_col: str = "decision_time",
) -> pd.Series:
    """Earliest reactive fire time (may be after event_end; caller must clip)."""
    decision = pd.to_numeric(events[decision_col], errors="coerce")
    seq = pd.to_numeric(events[seq_col], errors="coerce")
    retry_fire = decision.where(seq >= RETRY_SEQ_NO_MIN, pd.NA)
    fallback = float(runtime_medians.attrs.get("global_median", 0.0))
    mapped = events[group_col].map(runtime_medians)
    runtime = pd.to_numeric(mapped, errors="coerce").fillna(fallback)
    runtime_fire = decision + runtime
    return pd.concat([retry_fire, runtime_fire], axis=1).min(axis=1, skipna=True)
