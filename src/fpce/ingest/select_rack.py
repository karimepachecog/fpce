"""Select a homogeneous rack (~40 machines) from machine_meta events."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from fpce.config import DATA_PROCESSED, DATA_RAW, RACK_IDS_PATH, RACK_SIZE, TRACE_DURATION_SECONDS
from fpce.io import read_trace_csv, write_parquet


def reconstruct_machine_state(meta_path: Path) -> pd.DataFrame:
    """Reconstruct latest machine state from event table."""
    events = read_trace_csv(meta_path, "machine_meta")
    events = events.sort_values(["machine_id", "time_stamp"])
    latest = events.groupby("machine_id", as_index=False).tail(1)
    return latest


def select_rack(
    meta_path: Path,
    rack_size: int = RACK_SIZE,
    exclude_domains: set[int] | None = None,
) -> list[str]:
    """Pick homogeneous machines from the same failure domain."""
    latest = reconstruct_machine_state(meta_path)
    exclude_domains = exclude_domains or set()

    coverage = (
        latest.groupby(["failure_domain_1", "cpu_num", "mem_size"])
        .agg(count=("machine_id", "count"))
        .reset_index()
        .sort_values("count", ascending=False)
    )
    if coverage.empty:
        raise ValueError("No machines found in machine_meta")

    chosen = None
    for _, row in coverage.iterrows():
        domain = int(row["failure_domain_1"])
        if domain in exclude_domains:
            continue
        if int(row["count"]) >= min(rack_size, 1):
            chosen = row
            break
    if chosen is None:
        raise ValueError(
            f"No failure domain with machines available outside {exclude_domains}"
        )

    domain = int(chosen["failure_domain_1"])
    cpu_num = chosen["cpu_num"]
    mem_size = chosen["mem_size"]

    candidates = latest[
        (latest["failure_domain_1"] == domain)
        & (latest["cpu_num"] == cpu_num)
        & (latest["mem_size"] == mem_size)
    ].copy()

    event_counts = (
        read_trace_csv(meta_path, "machine_meta", usecols=["machine_id"])
        .groupby("machine_id")
        .size()
        .rename("event_count")
    )
    candidates = candidates.merge(event_counts, on="machine_id", how="left")
    candidates = candidates.sort_values("event_count", ascending=False)

    rack = candidates.head(rack_size)["machine_id"].tolist()
    if len(rack) < rack_size:
        print(
            f"[warn] Only {len(rack)} machines available in domain={domain}, "
            f"cpu={cpu_num}, mem={mem_size}; requested {rack_size}"
        )
    return rack


def save_rack_metadata(
    meta_path: Path,
    rack_ids: list[str],
    out_path: Path = RACK_IDS_PATH,
    machines_parquet: Path | None = None,
) -> dict:
    latest = reconstruct_machine_state(meta_path)
    rack_meta = latest[latest["machine_id"].isin(rack_ids)].copy()
    machines_path = machines_parquet or (out_path.parent / f"{out_path.stem}_machines.parquet")
    write_parquet(rack_meta, machines_path)

    payload = {
        "rack_size": len(rack_ids),
        "machine_ids": rack_ids,
        "failure_domain_1": int(rack_meta["failure_domain_1"].iloc[0]),
        "cpu_num": int(rack_meta["cpu_num"].iloc[0]),
        "mem_size": float(rack_meta["mem_size"].iloc[0]),
        "trace_duration_seconds": TRACE_DURATION_SECONDS,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[ok] Selected rack of {len(rack_ids)} machines -> {out_path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Select rack machines from machine_meta")
    parser.add_argument(
        "--meta",
        type=Path,
        default=DATA_RAW / "machine_meta.csv",
        help="Path to machine_meta.csv",
    )
    parser.add_argument("--rack-size", type=int, default=RACK_SIZE)
    parser.add_argument(
        "--exclude-domain",
        type=int,
        action="append",
        default=[],
        help="Failure domains to skip (repeatable)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=RACK_IDS_PATH,
        help="JSON file for selected rack machine ids",
    )
    parser.add_argument(
        "--machines-parquet",
        type=Path,
        default=None,
        help="Optional parquet path for rack machine metadata",
    )
    args = parser.parse_args()

    rack_ids = select_rack(
        args.meta,
        rack_size=args.rack_size,
        exclude_domains=set(args.exclude_domain),
    )
    save_rack_metadata(
        args.meta,
        rack_ids,
        out_path=args.output,
        machines_parquet=args.machines_parquet,
    )


if __name__ == "__main__":
    main()
