#!/usr/bin/env python3
"""Report failure_within_horizon positive rate at multiple prediction horizons."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from fpce.config import RACKS


def horizon_rates(grid_path: Path, horizons_seconds: list[int]) -> dict[int, float]:
    grid = pd.read_parquet(grid_path, columns=["seconds_to_next_failure"])
    stf = grid["seconds_to_next_failure"]
    valid = stf.notna()
    n = len(grid)
    rates = {}
    for h in horizons_seconds:
        rate = (stf <= h).sum() / n * 100
        rates[h] = round(float(rate), 4)
    return rates


def main() -> None:
    parser = argparse.ArgumentParser(description="Horizon sensitivity for label rate")
    parser.add_argument(
        "--horizons",
        type=int,
        nargs="+",
        default=[900, 1800, 3600],
        help="Horizons in seconds (default: 15, 30, 60 min)",
    )
    parser.add_argument(
        "--rack",
        choices=list(RACKS.keys()),
        action="append",
        default=None,
    )
    args = parser.parse_args()

    rack_names = args.rack or list(RACKS.keys())
    print("Horizon sensitivity (failure_within_horizon proxy from seconds_to_next_failure)")
    print(f"{'rack':<10} {'horizon':<12} {'rate_pct':<10}")
    print("-" * 34)
    for name in rack_names:
        grid_path = Path(RACKS[name]["output_dir"]) / "time_grid.parquet"
        if not grid_path.exists():
            print(f"[skip] {name}: missing {grid_path}")
            continue
        rates = horizon_rates(grid_path, args.horizons)
        for h, rate in rates.items():
            label = f"{h // 60} min" if h % 60 == 0 else f"{h}s"
            print(f"{name:<10} {label:<12} {rate:<10.4f}")


if __name__ == "__main__":
    main()
