"""Build 1-minute resampled time grid with failure labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from fpce.config import (
    ACTIVE_STATUSES,
    FAILURE_HORIZON_SECONDS,
    FAILURE_STATUSES,
    RACKS,
    RESAMPLE_INTERVAL_SECONDS,
    racks_of_kind,
)
from fpce.io import write_parquet


def load_rack_ids(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["machine_ids"]


def failure_timestamp(row: pd.Series) -> int | None:
    """Return Unix failure instant for labeled failure rows, else None.

    Alibaba batch_instance marks many Failed rows with end_time=0 while the
    instance was still running; in that case start_time is used as the failure
    proxy (documented limitation when exact failure time is not recorded).
    Rows with neither timestamp are excluded from failure labeling.
    """
    if row.get("status") not in FAILURE_STATUSES:
        return None
    end = row.get("end_time")
    start = row.get("start_time")
    if pd.notna(end) and float(end) > 0:
        return int(end)
    if pd.notna(start) and float(start) > 0:
        return int(start)
    return None


def instance_end(row: pd.Series, trace_max: int) -> float:
    """Effective interval end for active-instance counting."""
    end = row.get("end_time")
    start = row.get("start_time")
    status = row.get("status")

    if pd.notna(end) and float(end) > 0:
        return float(end)
    if status in ACTIVE_STATUSES:
        return float(trace_max)

    failure_ts = failure_timestamp(row)
    if failure_ts is not None:
        return float(failure_ts)
    if pd.notna(start) and float(start) > 0:
        return float(start)
    return float(trace_max)


def count_active_at_times(
    times: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
) -> np.ndarray:
    """Count how many instances are active at each resampled timestamp.

    Uses a difference array over the fixed minute grid (O(instances)) instead
    of broadcasting times x instances, which exhausts RAM on full racks.
    """
    n = len(times)
    if len(starts) == 0 or n == 0:
        return np.zeros(n, dtype=int)

    valid = (starts > 0) & (ends >= starts)
    if not valid.any():
        return np.zeros(n, dtype=int)

    starts = starts[valid]
    ends = ends[valid]
    minute_times = times.astype(np.float64)
    diff = np.zeros(n + 1, dtype=np.int64)

    for start, end in zip(starts, ends):
        i0 = int(np.searchsorted(minute_times, start, side="left"))
        i1 = int(np.searchsorted(minute_times, end, side="right")) - 1
        if i0 > i1 or i0 >= n:
            continue
        diff[i0] += 1
        diff[min(i1 + 1, n)] -= 1

    return np.cumsum(diff[:n]).astype(int)


def vectorized_failure_timestamps(inst: pd.DataFrame) -> pd.Series:
    status = inst["status"]
    start = pd.to_numeric(inst["start_time"], errors="coerce").fillna(0)
    end = pd.to_numeric(inst["end_time"], errors="coerce").fillna(0)
    is_fail = status.isin(FAILURE_STATUSES)
    ts = pd.Series(np.nan, index=inst.index, dtype=float)
    ts[is_fail & (end > 0)] = end[is_fail & (end > 0)]
    ts[is_fail & (end <= 0) & (start > 0)] = start[is_fail & (end <= 0) & (start > 0)]
    return ts


def vectorized_instance_ends(inst: pd.DataFrame, trace_max: int) -> pd.Series:
    status = inst["status"]
    start = pd.to_numeric(inst["start_time"], errors="coerce").fillna(0)
    end = pd.to_numeric(inst["end_time"], errors="coerce").fillna(0)
    failure_ts = vectorized_failure_timestamps(inst)

    eff = pd.Series(float(trace_max), index=inst.index, dtype=float)
    has_end = end > 0
    eff[has_end] = end[has_end]
    active = status.isin(ACTIVE_STATUSES) & ~has_end
    fail_open = failure_ts.notna() & ~has_end & ~active
    eff[fail_open] = failure_ts[fail_open]
    only_start = ~has_end & ~active & failure_ts.isna() & (start > 0)
    eff[only_start] = start[only_start]
    return eff


def load_instances_for_machine(instances_path: Path, machine_id: str) -> pd.DataFrame:
    table = pq.read_table(instances_path, filters=[("machine_id", "==", machine_id)])
    return table.to_pandas()


def build_usage_grid(
    usage_path: Path,
    rack_ids: list[str],
    interval: int = RESAMPLE_INTERVAL_SECONDS,
) -> tuple[pd.DataFrame, int]:
    usage = pd.read_parquet(usage_path)
    usage = usage[usage["machine_id"].isin(rack_ids)].copy()
    usage["time_stamp"] = usage["time_stamp"].astype(float)
    usage["minute"] = (usage["time_stamp"] // interval * interval).astype(int)

    numeric_cols = [
        "cpu_util_percent",
        "mem_util_percent",
        "mem_gps",
        "mkpi",
        "net_in",
        "net_out",
        "disk_io_percent",
    ]
    grid = (
        usage.groupby(["machine_id", "minute"], as_index=False)[numeric_cols]
        .mean(numeric_only=True)
        .rename(columns={"minute": "time_stamp"})
    )
    trace_max = int(usage["time_stamp"].max()) if not usage.empty else 0
    return grid, trace_max


def add_failure_labels(
    grid_m: pd.DataFrame,
    failure_times: list[int],
    horizon: int,
) -> pd.DataFrame:
    if not failure_times:
        grid_m = grid_m.copy()
        grid_m["seconds_to_next_failure"] = np.nan
        grid_m["failure_within_horizon"] = 0
        return grid_m

    ft = np.array(sorted(failure_times), dtype=np.int64)
    t = grid_m["time_stamp"].to_numpy(dtype=np.int64)
    idx = np.searchsorted(ft, t, side="left")

    seconds = np.full(len(t), np.nan, dtype=float)
    in_window = np.zeros(len(t), dtype=int)
    valid = idx < len(ft)
    seconds[valid] = ft[idx[valid]] - t[valid]
    in_window[valid] = (seconds[valid] <= horizon).astype(int)

    grid_m = grid_m.copy()
    grid_m["seconds_to_next_failure"] = seconds
    grid_m["failure_within_horizon"] = in_window
    return grid_m


def build_machine_grid(
    grid_m: pd.DataFrame,
    inst_m: pd.DataFrame,
    trace_max: int,
    horizon: int = FAILURE_HORIZON_SECONDS,
) -> pd.DataFrame:
    numeric_cols = [
        "cpu_util_percent",
        "mem_util_percent",
        "mem_gps",
        "mkpi",
        "net_in",
        "net_out",
        "disk_io_percent",
    ]

    inst_m = inst_m.copy()
    inst_m["failure_ts"] = vectorized_failure_timestamps(inst_m)
    effective_end = vectorized_instance_ends(inst_m, trace_max)

    times = grid_m["time_stamp"].to_numpy(dtype=float)
    counts = count_active_at_times(
        times,
        inst_m["start_time"].fillna(0).to_numpy(dtype=float),
        effective_end.to_numpy(dtype=float),
    )

    failure_times = sorted(inst_m.loc[inst_m["failure_ts"].notna(), "failure_ts"].astype(int).unique())

    out = grid_m.copy()
    out["active_instances"] = counts
    out = add_failure_labels(out, failure_times, horizon)
    out["data_gap"] = out[numeric_cols].isna().all(axis=1).astype(int)
    return out


def merge_time_grid_chunks(chunk_dir: Path, output_path: Path) -> pd.DataFrame:
    paths = sorted(chunk_dir.glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"No chunk parquets in {chunk_dir}")
    frames = [pd.read_parquet(path) for path in paths]
    grid = pd.concat(frames, ignore_index=True)
    grid = grid.sort_values(["time_stamp", "machine_id"]).reset_index(drop=True)
    write_parquet(grid, output_path)
    return grid


def build_time_grid(
    usage_path: Path,
    instances_path: Path,
    rack_ids: list[str],
    interval: int = RESAMPLE_INTERVAL_SECONDS,
    horizon: int = FAILURE_HORIZON_SECONDS,
    chunk_dir: Path | None = None,
) -> pd.DataFrame:
    grid, trace_max = build_usage_grid(usage_path, rack_ids, interval=interval)

    if chunk_dir is not None:
        chunk_dir.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    for machine_id in rack_ids:
        mask = grid["machine_id"] == machine_id
        if not mask.any():
            continue

        inst_m = load_instances_for_machine(instances_path, machine_id)
        grid_m = build_machine_grid(grid.loc[mask].copy(), inst_m, trace_max, horizon=horizon)
        del inst_m

        if chunk_dir is not None:
            chunk_path = chunk_dir / f"{machine_id}.parquet"
            write_parquet(grid_m, chunk_path)
            print(f"[ok] chunk: {machine_id} -> {chunk_path} ({len(grid_m):,} rows)")
        else:
            frames.append(grid_m)

    if chunk_dir is not None:
        return pd.DataFrame()

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(
        ["time_stamp", "machine_id"]
    ).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build resampled time grid")
    parser.add_argument(
        "--rack",
        choices=list(racks_of_kind("alibaba")),
        default="primary",
        help="Rack to build time grid for",
    )
    parser.add_argument(
        "--machine-id",
        action="append",
        default=None,
        help="Process only these machine IDs (repeatable). Default: all rack machines.",
    )
    parser.add_argument(
        "--chunk-dir",
        type=Path,
        default=None,
        help="Write one parquet per machine here (low memory mode)",
    )
    parser.add_argument(
        "--merge-chunks",
        type=Path,
        default=None,
        help="Merge chunk parquets from this directory into --output",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Override output parquet path",
    )
    args = parser.parse_args()

    rack_cfg = RACKS[args.rack]
    output_dir = Path(rack_cfg["output_dir"])
    all_rack_ids = load_rack_ids(Path(rack_cfg["ids_path"]))
    rack_ids = args.machine_id or all_rack_ids
    out_path = args.output or (output_dir / "time_grid.parquet")

    if args.merge_chunks is not None:
        grid = merge_time_grid_chunks(args.merge_chunks, out_path)
        pos_rate = grid["failure_within_horizon"].mean() * 100
        print(
            f"[ok] time_grid ({args.rack}): merged {len(grid):,} rows -> {out_path} "
            f"(failure_within_horizon={pos_rate:.4f}%)"
        )
        return

    if args.machine_id and len(args.machine_id) == 1 and args.chunk_dir:
        usage_path = output_dir / "machine_usage.parquet"
        instances_path = output_dir / "batch_instance.parquet"
        grid, trace_max = build_usage_grid(usage_path, rack_ids)
        machine_id = args.machine_id[0]
        mask = grid["machine_id"] == machine_id
        if not mask.any():
            raise ValueError(f"No usage rows for machine {machine_id}")
        inst_m = load_instances_for_machine(instances_path, machine_id)
        grid_m = build_machine_grid(grid.loc[mask].copy(), inst_m, trace_max)
        chunk_path = args.chunk_dir / f"{machine_id}.parquet"
        write_parquet(grid_m, chunk_path)
        print(f"[ok] chunk: {machine_id} -> {chunk_path} ({len(grid_m):,} rows)")
        return

    grid = build_time_grid(
        output_dir / "machine_usage.parquet",
        output_dir / "batch_instance.parquet",
        rack_ids,
        chunk_dir=args.chunk_dir,
    )
    if args.chunk_dir is not None:
        print(
            f"[ok] wrote {len(list(args.chunk_dir.glob('*.parquet')))} chunks "
            f"under {args.chunk_dir}"
        )
        return

    write_parquet(grid, out_path)
    pos_rate = grid["failure_within_horizon"].mean() * 100
    print(
        f"[ok] time_grid ({args.rack}): {len(grid):,} rows -> {out_path} "
        f"(failure_within_horizon={pos_rate:.4f}%)"
    )


if __name__ == "__main__":
    main()
