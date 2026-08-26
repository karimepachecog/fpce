#!/usr/bin/env python3
"""Write data/processed/HANDOFF.md with SHA256 checksums of the USB kit."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fpce.config import DATA_PROCESSED, PROJECT_ROOT, repo_relpath

KIT = [
    DATA_PROCESSED / "primary" / "instance_events.parquet",
    DATA_PROCESSED / "primary" / "time_grid.parquet",
    DATA_PROCESSED / "replication" / "instance_events.parquet",
    DATA_PROCESSED / "replication" / "time_grid.parquet",
    DATA_PROCESSED / "google" / "attempts.parquet",
    DATA_PROCESSED / "google" / "attempts_sample.parquet",
    DATA_PROCESSED / "google" / "export_manifest.json",
    DATA_PROCESSED / "spec_power_curves.parquet",
    DATA_PROCESSED / "primary_time_split.json",
    DATA_PROCESSED / "rack_machine_ids.json",
    DATA_PROCESSED / "replication_rack_machine_ids.json",
]


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    lines = [
        "# Role A USB handoff",
        "",
        "Copy these files onto a USB stick (8 GB is enough; 16 GB is comfortable).",
        "After cloning the GitHub repo, overlay them onto `data/processed/`.",
        "",
        "Do **not** copy `data/raw/`, `data/interim/`, `batch_instance.parquet`,",
        "`batch_task.parquet`, `machine_usage.parquet`, or `time_grid_chunks/`.",
        "",
        "The kit including the Google laptop sample (~80 MB) is about **4.8 GB**.",
        "",
        "| File | Bytes | SHA256 |",
        "|------|------:|--------|",
    ]
    missing: list[str] = []
    total = 0
    for path in KIT:
        rel = repo_relpath(path)
        if not path.exists():
            missing.append(rel)
            lines.append(f"| `{rel}` | missing | — |")
            continue
        size = path.stat().st_size
        total += size
        lines.append(f"| `{rel}` | {size:,} | `{_sha256(path)}` |")
    lines.extend(
        [
            "",
            f"Total present: **{total / (1 << 30):.2f} GiB** ({total:,} bytes).",
            "",
            "After copying, compare each SHA256 above. Role B on a laptop should point",
            "`fpce-cross-provider` at `attempts_sample.parquet` (the default if that file exists).",
            "Use the full `attempts.parquet` only on a machine with 16+ GB RAM.",
            "",
        ]
    )
    out = DATA_PROCESSED / "HANDOFF.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] {repo_relpath(out)} ({total / (1 << 30):.2f} GiB listed)")
    if missing:
        print("[warn] missing: " + ", ".join(missing))
        raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main() or 0)
