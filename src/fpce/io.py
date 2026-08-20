"""Shared utilities for CSV/Parquet ingestion."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fpce.config import NORMALIZED_COLUMNS, SENTINEL_VALUES, TABLE_SCHEMAS


def read_trace_csv(path: Path, table: str, usecols: list[str] | None = None) -> pd.DataFrame:
    """Read a headerless Alibaba trace CSV with schema columns."""
    columns = TABLE_SCHEMAS[table]
    df = pd.read_csv(
        path,
        header=None,
        names=columns,
        usecols=usecols,
        low_memory=False,
    )
    return clean_trace_df(df, table)


def clean_trace_df(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """Replace sentinel values with NA on normalized columns."""
    df = df.copy()
    for col in NORMALIZED_COLUMNS.get(table, []):
        if col in df.columns:
            df[col] = df[col].replace(list(SENTINEL_VALUES), pd.NA)
    return df


def write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, engine="pyarrow")
