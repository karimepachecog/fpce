"""Create a frozen time-based train/test split for the primary rack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from fpce.config import DATA_PROCESSED, RACKS, TRAIN_FRACTION


def make_time_split(
    grid_path: Path,
    train_fraction: float = TRAIN_FRACTION,
    instance_path: Path | None = None,
) -> dict:
    grid = pd.read_parquet(grid_path, columns=["time_stamp", "machine_id"])
    t_min = int(grid["time_stamp"].min())
    t_max = int(grid["time_stamp"].max())
    split_at = int(t_min + (t_max - t_min) * train_fraction)

    train = grid["time_stamp"] < split_at
    payload: dict = {
        "grid_path": str(grid_path),
        "time_min": t_min,
        "time_max": t_max,
        "split_timestamp": split_at,
        "train_fraction": train_fraction,
        "grid_train_rows": int(train.sum()),
        "grid_test_rows": int((~train).sum()),
        "n_machines": int(grid["machine_id"].nunique()),
        "rule": {
            "grid": "time_stamp < split_timestamp",
            "instances": "start_time < split_timestamp",
        },
    }

    if instance_path is not None and instance_path.exists():
        events = pd.read_parquet(
            instance_path,
            columns=["start_time", "failed", "eligible_for_training", "eligible_for_costing"],
        )
        inst_train = events["start_time"] < split_at
        trainable = events["eligible_for_training"] == 1
        payload["instance_events_path"] = str(instance_path)
        payload["instance_train_rows"] = int(inst_train.sum())
        payload["instance_test_rows"] = int((~inst_train).sum())
        payload["instance_train_positive_rate"] = round(
            float(events.loc[inst_train & trainable, "failed"].mean() * 100), 4
        ) if (inst_train & trainable).any() else None
        payload["instance_test_positive_rate"] = round(
            float(events.loc[(~inst_train) & trainable, "failed"].mean() * 100), 4
        ) if ((~inst_train) & trainable).any() else None
        payload["instance_train_costing_rows"] = int(
            (inst_train & (events["eligible_for_costing"] == 1)).sum()
        )
        payload["instance_test_costing_rows"] = int(
            ((~inst_train) & (events["eligible_for_costing"] == 1)).sum()
        )

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze time-based train/test split")
    parser.add_argument("--rack", choices=list(RACKS.keys()), default="primary")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    rack_cfg = RACKS[args.rack]
    output_dir = Path(rack_cfg["output_dir"])
    grid_path = output_dir / "time_grid.parquet"
    instance_path = output_dir / "instance_events.parquet"
    out_path = args.output or (DATA_PROCESSED / f"{args.rack}_time_split.json")

    payload = make_time_split(grid_path, instance_path=instance_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    extra = ""
    if "instance_train_rows" in payload:
        extra = (
            f" instances train={payload['instance_train_rows']:,} "
            f"test={payload['instance_test_rows']:,}"
        )
    print(
        f"[ok] Split at t={payload['split_timestamp']}: "
        f"grid train={payload['grid_train_rows']:,} "
        f"test={payload['grid_test_rows']:,}{extra} -> {out_path}"
    )


if __name__ == "__main__":
    main()
