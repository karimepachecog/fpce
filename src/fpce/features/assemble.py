"""Assemble primary-rack train/test matrices (no model fitting).

Loads only contract-allowed columns, filters eligible_for_training, joins
host state with join_host_at_decision, and splits on the frozen timestamp.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from fpce.config import DATA_PROCESSED, RACKS, resolve_repo_path
from fpce.contracts import FeatureContract, load_feature_contract
from fpce.features.windows import join_host_at_decision

TARGET = "failed"
ELIGIBLE = "eligible_for_training"


@dataclass(frozen=True)
class PreparedSplit:
    """Train/test frames after leakage checks. Not a fitted model."""

    feature_columns: list[str]
    target: str
    X_train: pd.DataFrame
    X_test: pd.DataFrame
    y_train: pd.Series
    y_test: pd.Series
    split_timestamp: int
    split_column: str
    t_train: pd.Series | None = None
    t_test: pd.Series | None = None


def load_time_split(path: Path | None = None) -> dict:
    path = path or (DATA_PROCESSED / "primary_time_split.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_names(path: Path) -> set[str]:
    return set(pq.read_schema(path).names)


def instance_load_columns(schema_names: set[str], contract: FeatureContract) -> list[str]:
    """Keys + target + allowed instance features present on disk."""
    needed = [
        ELIGIBLE,
        contract.label,
        contract.split_column,
        contract.decision_time_column,
        "machine_id",
        *[c for c in contract.allow if c in schema_names],
    ]
    ordered: list[str] = []
    seen: set[str] = set()
    for col in needed:
        if col in schema_names and col not in seen:
            ordered.append(col)
            seen.add(col)
    missing = [c for c in (ELIGIBLE, contract.label, contract.split_column, contract.decision_time_column, "machine_id") if c not in schema_names]
    if missing:
        raise ValueError(f"instance events missing required columns: {missing}")
    return ordered


def grid_load_columns(schema_names: set[str], contract: FeatureContract) -> list[str]:
    cols = ["machine_id", "time_stamp"]
    cols.extend(c for c in contract.allow_from_time_grid if c in schema_names)
    missing = [c for c in ("machine_id", "time_stamp") if c not in schema_names]
    if missing:
        raise ValueError(f"time grid missing required columns: {missing}")
    return list(dict.fromkeys(cols))


def load_trainable_events(
    path: Path,
    contract: FeatureContract,
    extra_columns: tuple[str, ...] = (),
) -> pd.DataFrame:
    names = _schema_names(path)
    columns = instance_load_columns(names, contract)
    for col in extra_columns:
        if col in names and col not in columns:
            columns.append(col)
    table = ds.dataset(path, format="parquet").to_table(
        columns=columns,
        filter=pc.field(ELIGIBLE) == 1,
    )
    return table.to_pandas()


def load_host_grid(path: Path, contract: FeatureContract) -> pd.DataFrame:
    names = _schema_names(path)
    columns = grid_load_columns(names, contract)
    table = ds.dataset(path, format="parquet").to_table(columns=columns)
    return table.to_pandas()


def assemble_feature_frame(
    events: pd.DataFrame,
    grid: pd.DataFrame,
    contract: FeatureContract | None = None,
) -> pd.DataFrame:
    """As-of join host columns; does not drop split/target keys."""
    contract = contract or load_feature_contract()
    host_cols = [c for c in contract.allow_from_time_grid if c in grid.columns]
    joined = join_host_at_decision(events, grid, columns=host_cols)
    if ELIGIBLE in joined.columns:
        joined = joined.loc[joined[ELIGIBLE] == 1].drop(columns=[ELIGIBLE])
    return joined.reset_index(drop=True)


def select_feature_columns(frame: pd.DataFrame, contract: FeatureContract) -> list[str]:
    """Allow-listed columns only; split/target keys may remain on the frame.

    Do not pass the full frame to `allowed_columns`: that helper treats every
    name as a feature and would flag `failed` as leakage.
    """
    allowed = contract.allow | contract.allow_from_time_grid
    features = [c for c in frame.columns if c in allowed]
    if not features:
        raise ValueError("no allowed feature columns present after join")
    contract.assert_no_leakage(features)
    return features


def split_prepared(
    frame: pd.DataFrame,
    *,
    split_timestamp: int,
    contract: FeatureContract,
) -> PreparedSplit:
    features = select_feature_columns(frame, contract)
    split_col = contract.split_column
    target = contract.label
    train_mask = frame[split_col] < split_timestamp
    test_mask = ~train_mask
    return PreparedSplit(
        feature_columns=features,
        target=target,
        X_train=frame.loc[train_mask, features].reset_index(drop=True),
        X_test=frame.loc[test_mask, features].reset_index(drop=True),
        y_train=frame.loc[train_mask, target].reset_index(drop=True),
        y_test=frame.loc[test_mask, target].reset_index(drop=True),
        split_timestamp=int(split_timestamp),
        split_column=split_col,
        t_train=frame.loc[train_mask, split_col].reset_index(drop=True),
        t_test=frame.loc[test_mask, split_col].reset_index(drop=True),
    )


def prepare_rack_training(
    *,
    events_path: Path,
    grid_path: Path,
    split_path: Path | None = None,
    contract: FeatureContract | None = None,
) -> PreparedSplit:
    contract = contract or load_feature_contract()
    split = load_time_split(split_path)
    events = load_trainable_events(events_path, contract)
    grid = load_host_grid(grid_path, contract)
    frame = assemble_feature_frame(events, grid, contract)
    return split_prepared(
        frame,
        split_timestamp=int(split["split_timestamp"]),
        contract=contract,
    )


def prepare_primary_training(
    *,
    events_path: Path | None = None,
    grid_path: Path | None = None,
    split_path: Path | None = None,
) -> PreparedSplit:
    output_dir = Path(RACKS["primary"]["output_dir"])
    split = load_time_split(split_path)
    events_path = events_path or resolve_repo_path(split.get("instance_events_path", output_dir / "instance_events.parquet"))
    grid_path = grid_path or resolve_repo_path(split.get("grid_path", output_dir / "time_grid.parquet"))
    return prepare_rack_training(
        events_path=events_path,
        grid_path=grid_path,
        split_path=split_path,
    )


def _positive_stats(y: pd.Series) -> dict:
    n = int(len(y))
    n_pos = int(pd.to_numeric(y, errors="coerce").fillna(0).astype("int64").sum())
    pct = (100.0 * n_pos / n) if n else 0.0
    return {"n": n, "n_positive": n_pos, "positive_pct": round(pct, 6)}


def _nulls_by_column(df: pd.DataFrame) -> dict[str, dict]:
    n = len(df)
    out: dict[str, dict] = {}
    for col in df.columns:
        n_null = int(df[col].isna().sum())
        out[col] = {
            "nulls": n_null,
            "null_pct": round((100.0 * n_null / n) if n else 0.0, 4),
        }
    return out


def describe_prepared(prepared: PreparedSplit) -> dict:
    X_all = pd.concat([prepared.X_train, prepared.X_test], ignore_index=True)
    return {
        "feature_columns": prepared.feature_columns,
        "target": prepared.target,
        "split_column": prepared.split_column,
        "split_timestamp": prepared.split_timestamp,
        "train": _positive_stats(prepared.y_train),
        "test": _positive_stats(prepared.y_test),
        "nulls_by_feature": {
            "train": _nulls_by_column(prepared.X_train),
            "test": _nulls_by_column(prepared.X_test),
            "all": _nulls_by_column(X_all),
        },
        "shapes": {
            "X_train": list(prepared.X_train.shape),
            "X_test": list(prepared.X_test.shape),
            "y_train": list(prepared.y_train.shape),
            "y_test": list(prepared.y_test.shape),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble primary-rack train/test matrices; do not fit a model."
    )
    parser.add_argument("--events", type=Path, default=None)
    parser.add_argument("--grid", type=Path, default=None)
    parser.add_argument("--split", type=Path, default=None)
    args = parser.parse_args()
    prepared = prepare_primary_training(
        events_path=args.events,
        grid_path=args.grid,
        split_path=args.split,
    )
    print(json.dumps(describe_prepared(prepared), indent=2))


if __name__ == "__main__":
    main()
