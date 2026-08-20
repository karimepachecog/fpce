"""Filter batch_instance to rack machines in streaming chunks."""

from __future__ import annotations

import argparse
import json
import tarfile
from collections import defaultdict
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from fpce.config import DATA_INTERIM, DATA_RAW, RACK_IDS_PATH, TRACE_FILES
from fpce.io import clean_trace_df

COLUMNS = [
    "instance_name",
    "task_name",
    "job_name",
    "task_type",
    "status",
    "start_time",
    "end_time",
    "machine_id",
    "seq_no",
    "total_seq_no",
    "cpu_avg",
    "cpu_max",
    "mem_avg",
    "mem_max",
]


def load_rack_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return set(payload["machine_ids"])


def _open_source(source: Path):
    if source.suffixes[-2:] == [".tar", ".gz"] or source.name.endswith(".tar.gz"):
        tar = tarfile.open(source, "r:gz")
        csv_name = TRACE_FILES["batch_instance"]["csv_name"]
        member = tar.getmember(csv_name)
        stream = tar.extractfile(member)
        if stream is None:
            tar.close()
            raise RuntimeError(f"Could not extract {csv_name} from {source}")
        return tar, stream
    return None, open(source, "rb")


def filter_instances_multi(
    source: Path,
    rack_dest: dict[str, tuple[set[str], Path]],
    chunksize: int = 500_000,
) -> dict[str, int]:
    """Filter batch_instance once, writing one CSV per rack."""
    union_ids: set[str] = set()
    for ids, dest in rack_dest.values():
        union_ids |= ids
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()

    machine_to_rack: dict[str, str] = {}
    for rack_name, (ids, _) in rack_dest.items():
        for machine_id in ids:
            machine_to_rack[machine_id] = rack_name

    first_write = {name: True for name in rack_dest}
    totals = defaultdict(int)
    rows_scanned = 0

    tar_handle, raw_stream = _open_source(source)
    try:
        reader = pd.read_csv(
            raw_stream,
            header=None,
            names=COLUMNS,
            chunksize=chunksize,
            low_memory=False,
        )
        for chunk in tqdm(reader, desc="filter batch_instance"):
            rows_scanned += len(chunk)
            filtered = chunk[chunk["machine_id"].isin(union_ids)]
            if filtered.empty:
                continue
            filtered = clean_trace_df(filtered, "batch_instance")
            for rack_name, (_, dest) in rack_dest.items():
                rack_chunk = filtered[
                    filtered["machine_id"].isin(rack_dest[rack_name][0])
                ]
                if rack_chunk.empty:
                    continue
                rack_chunk.to_csv(
                    dest,
                    mode="a",
                    header=first_write[rack_name],
                    index=False,
                )
                first_write[rack_name] = False
                totals[rack_name] += len(rack_chunk)
    finally:
        if hasattr(raw_stream, "close"):
            raw_stream.close()
        if tar_handle is not None:
            tar_handle.close()

    print(f"[ok] Scanned {rows_scanned:,} instance rows from {source}")
    for rack_name, count in totals.items():
        dest = rack_dest[rack_name][1]
        print(f"[ok] {rack_name}: {count:,} rows -> {dest}")
    return dict(totals)


def filter_instances(
    source: Path,
    rack_ids: set[str],
    dest: Path,
    chunksize: int = 500_000,
) -> int:
    totals = filter_instances_multi(
        source,
        {"default": (rack_ids, dest)},
        chunksize=chunksize,
    )
    return totals.get("default", 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter batch_instance to rack(s)")
    parser.add_argument(
        "--source",
        type=Path,
        default=DATA_RAW / "batch_instance.tar.gz",
        help="batch_instance.csv or batch_instance.tar.gz",
    )
    parser.add_argument(
        "--rack-ids",
        type=Path,
        action="append",
        default=[RACK_IDS_PATH],
        help="Rack JSON file(s); repeat for multiple racks",
    )
    parser.add_argument(
        "--dest-dir",
        type=Path,
        default=DATA_INTERIM,
        help="Directory for filtered CSV outputs",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Single output CSV (only when one --rack-ids is given)",
    )
    args = parser.parse_args()

    if len(args.rack_ids) == 1 and args.dest is not None:
        rack_ids = load_rack_ids(args.rack_ids[0])
        filter_instances(args.source, rack_ids, args.dest)
        return

    rack_dest: dict[str, tuple[set[str], Path]] = {}
    for rack_path in args.rack_ids:
        rack_name = rack_path.stem.replace("_machine_ids", "").replace("_rack", "")
        if rack_name.endswith("_ids"):
            rack_name = rack_path.stem
        dest = args.dest_dir / f"batch_instance_{rack_path.stem}.csv"
        rack_dest[rack_name] = (load_rack_ids(rack_path), dest)

    filter_instances_multi(args.source, rack_dest)


if __name__ == "__main__":
    main()
