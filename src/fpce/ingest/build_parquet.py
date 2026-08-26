"""Normalize trace tables to Parquet for a selected rack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from fpce.config import DATA_INTERIM, DATA_PROCESSED, DATA_RAW, RACKS, racks_of_kind
from fpce.io import clean_trace_df, write_parquet


def load_rack_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return set(payload["machine_ids"])


def filter_machine_usage(source: Path, rack_ids: set[str], dest: Path) -> None:
    chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        source,
        header=None,
        names=[
            "machine_id",
            "time_stamp",
            "cpu_util_percent",
            "mem_util_percent",
            "mem_gps",
            "mkpi",
            "net_in",
            "net_out",
            "disk_io_percent",
        ],
        chunksize=1_000_000,
        low_memory=False,
    ):
        filtered = chunk[chunk["machine_id"].isin(rack_ids)]
        if not filtered.empty:
            chunks.append(filtered)

    df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    if not df.empty:
        df = clean_trace_df(df, "machine_usage")
        df = df.sort_values(["machine_id", "time_stamp"]).drop_duplicates(
            ["machine_id", "time_stamp"], keep="last"
        )
    write_parquet(df, dest)
    print(f"[ok] machine_usage: {len(df):,} rows -> {dest}")


def build_tasks(source: Path, dest: Path) -> None:
    df = pd.read_csv(
        source,
        header=None,
        names=[
            "task_name",
            "instance_num",
            "job_name",
            "task_type",
            "status",
            "start_time",
            "end_time",
            "plan_cpu",
            "plan_mem",
        ],
        low_memory=False,
    )
    df = clean_trace_df(df, "batch_task")
    df = df.sort_values(["job_name", "task_name", "start_time"])
    write_parquet(df, dest)
    print(f"[ok] batch_task: {len(df):,} rows -> {dest}")


def build_instances(source: Path, dest: Path) -> None:
    df = pd.read_csv(source, low_memory=False)
    df = clean_trace_df(df, "batch_instance")
    df = df.sort_values(["machine_id", "start_time", "instance_name"])
    write_parquet(df, dest)
    print(f"[ok] batch_instance: {len(df):,} rows -> {dest}")


def build_rack_tables(
    rack_name: str,
    interim_csv: Path | None = None,
) -> None:
    rack_cfg = RACKS[rack_name]
    output_dir = Path(rack_cfg["output_dir"])
    ids_path = Path(rack_cfg["ids_path"])
    rack_ids = load_rack_ids(ids_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    filter_machine_usage(
        DATA_RAW / "machine_usage.csv",
        rack_ids,
        output_dir / "machine_usage.parquet",
    )
    build_tasks(DATA_RAW / "batch_task.csv", output_dir / "batch_task.parquet")

    instance_csv = interim_csv or (
        DATA_INTERIM / f"batch_instance_{ids_path.stem}.csv"
    )
    if not instance_csv.exists():
        raise FileNotFoundError(
            f"Filtered instance CSV not found for rack {rack_name}: {instance_csv}"
        )
    build_instances(instance_csv, output_dir / "batch_instance.parquet")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build processed Parquet tables")
    parser.add_argument(
        "--rack",
        choices=list(racks_of_kind("alibaba")),
        action="append",
        default=None,
        help="Rack(s) to build (default: all Alibaba racks)",
    )
    parser.add_argument(
        "--instance-csv",
        type=Path,
        default=None,
        help="Override filtered batch_instance CSV for single-rack builds",
    )
    args = parser.parse_args()

    rack_names = args.rack or list(racks_of_kind("alibaba"))
    for rack_name in rack_names:
        print(f"[build] rack={rack_name}")
        interim = args.instance_csv if len(rack_names) == 1 else None
        build_rack_tables(rack_name, interim_csv=interim)


if __name__ == "__main__":
    main()
