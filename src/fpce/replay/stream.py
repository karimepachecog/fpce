"""Replay rack telemetry as a JSONL stream."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

from fpce.config import DATA_PROCESSED, RACKS


def replay_stream(
    grid_path: Path,
    speed: float = 60.0,
    start: int | None = None,
    end: int | None = None,
):
    """Yield rack snapshots ordered by timestamp.

    speed: simulated seconds per real second (60 = 1 hour/minute).
    """
    grid = pd.read_parquet(grid_path)
    if start is not None:
        grid = grid[grid["time_stamp"] >= start]
    if end is not None:
        grid = grid[grid["time_stamp"] <= end]

    timestamps = sorted(grid["time_stamp"].unique())
    if not timestamps:
        return

    t0_real = time.time()
    t0_sim = timestamps[0]

    for ts in timestamps:
        if speed > 0:
            target_real = t0_real + (ts - t0_sim) / speed
            delay = target_real - time.time()
            if delay > 0:
                time.sleep(delay)

        snapshot = grid[grid["time_stamp"] == ts]
        machines = snapshot.to_dict(orient="records")
        payload = {
            "time_stamp": int(ts),
            "rack_size": len(machines),
            "machines": machines,
        }
        yield payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay rack telemetry stream")
    parser.add_argument(
        "--grid",
        type=Path,
        default=DATA_PROCESSED / "primary" / "time_grid.parquet",
    )
    parser.add_argument(
        "--rack",
        choices=list(RACKS.keys()),
        default=None,
        help="Use time_grid.parquet for this rack (overrides --grid default)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write JSONL to file instead of stdout",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=0.0,
        help="Simulated seconds per real second; 0 = as fast as possible",
    )
    parser.add_argument("--start", type=int, default=None)
    parser.add_argument("--end", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Max snapshots to emit")
    args = parser.parse_args()

    grid_path = args.grid
    if args.rack is not None:
        grid_path = Path(RACKS[args.rack]["output_dir"]) / "time_grid.parquet"

    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    try:
        count = 0
        for payload in replay_stream(
            grid_path, speed=args.speed, start=args.start, end=args.end
        ):
            out.write(json.dumps(payload) + "\n")
            count += 1
            if args.limit and count >= args.limit:
                break
        if args.output:
            print(f"[ok] Wrote {count} snapshots -> {args.output}", file=sys.stderr)
    finally:
        if args.output:
            out.close()


if __name__ == "__main__":
    main()
