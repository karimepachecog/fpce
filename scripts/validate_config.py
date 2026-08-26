#!/usr/bin/env python3
"""Validate FPCE ingest configuration and coefficient registry."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fpce.config import (
    DATA_INTERIM,
    DATA_RAW,
    FAILURE_STATUSES,
    FEATURE_CONTRACT_JSON,
    PHYSICAL_COST_TOML,
    RACKS,
    RACK_SIZE,
    TRACE_DURATION_SECONDS,
    TRACE_FILES,
    racks_of_kind,
)
from fpce.contracts import load_feature_contract
from fpce.costing.coefficients import load_physical_cost_params, validation_warnings


def load_rack_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing rack ids file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def expected_instance_csv(ids_path: Path) -> Path:
    return DATA_INTERIM / f"batch_instance_{ids_path.stem}.csv"


def validate_rack(name: str, cfg: dict) -> list[str]:
    errors: list[str] = []
    ids_path = Path(cfg["ids_path"])
    output_dir = Path(cfg["output_dir"])

    try:
        payload = load_rack_json(ids_path)
    except FileNotFoundError as exc:
        return [str(exc)]

    machine_ids = payload.get("machine_ids", [])
    if len(machine_ids) != RACK_SIZE:
        errors.append(
            f"{name}: expected {RACK_SIZE} machines, got {len(machine_ids)} in {ids_path}"
        )
    if payload.get("rack_size") != len(machine_ids):
        errors.append(f"{name}: rack_size field does not match machine_ids length")

    expected_csv = expected_instance_csv(ids_path)
    if expected_csv.exists() and expected_csv.stat().st_size == 0:
        errors.append(f"{name}: filtered CSV exists but is empty: {expected_csv}")

    for artifact in (
        "machine_usage.parquet",
        "batch_instance.parquet",
        "batch_task.parquet",
        "time_grid.parquet",
    ):
        path = output_dir / artifact
        if path.exists() and path.stat().st_size == 0:
            errors.append(f"{name}: output exists but is empty: {path}")

    return errors


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    required_rack_keys = {"ids_path", "output_dir", "label"}
    for name, cfg in racks_of_kind("alibaba").items():
        missing = required_rack_keys - set(cfg)
        if missing:
            errors.append(f"RACKS[{name!r}] missing keys: {sorted(missing)}")
            continue
        errors.extend(validate_rack(name, cfg))

    google = RACKS.get("google")
    if google is not None:
        if "output_dir" not in google or "label" not in google:
            errors.append("RACKS['google'] must define output_dir and label")

    if FAILURE_STATUSES != {"Failed", "Interrupted"}:
        errors.append(
            f"FAILURE_STATUSES should be Failed/Interrupted only, got {FAILURE_STATUSES}"
        )

    primary = load_rack_json(Path(RACKS["primary"]["ids_path"]))
    replication = load_rack_json(Path(RACKS["replication"]["ids_path"]))
    overlap = set(primary["machine_ids"]) & set(replication["machine_ids"])
    if overlap:
        errors.append(
            f"Primary and replication racks share machines: {sorted(overlap)[:5]}..."
        )
    if primary.get("failure_domain_1") == replication.get("failure_domain_1"):
        errors.append("Primary and replication racks share the same failure domain")

    for table in ("machine_meta", "machine_usage", "batch_task"):
        csv_path = DATA_RAW / TRACE_FILES[table]["csv_name"]
        if not csv_path.exists():
            errors.append(f"Missing raw table required before build: {csv_path}")

    if primary.get("trace_duration_seconds") != TRACE_DURATION_SECONDS:
        errors.append("Primary rack trace_duration_seconds does not match config")

    if not FEATURE_CONTRACT_JSON.exists():
        errors.append(f"Missing feature contract: {FEATURE_CONTRACT_JSON}")
    else:
        try:
            contract = load_feature_contract(FEATURE_CONTRACT_JSON)
            if "failed" not in contract.deny or "cpu_avg" not in contract.deny:
                errors.append("feature contract must deny label and post-hoc telemetry columns")
        except (ValueError, KeyError) as exc:
            errors.append(f"Invalid {FEATURE_CONTRACT_JSON}: {exc}")

    if not PHYSICAL_COST_TOML.exists():
        errors.append(f"Missing coefficient registry: {PHYSICAL_COST_TOML}")
    else:
        try:
            load_physical_cost_params(PHYSICAL_COST_TOML)
            warnings.extend(validation_warnings(PHYSICAL_COST_TOML))
        except (ValueError, KeyError) as exc:
            errors.append(f"Invalid {PHYSICAL_COST_TOML}: {exc}")

    if errors:
        print("[fail] Configuration validation failed:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("[ok] FPCE configuration validated")
    print(f"  racks: {', '.join(racks_of_kind('alibaba'))}")
    print(f"  primary domain: {primary.get('failure_domain_1')}")
    print(f"  replication domain: {replication.get('failure_domain_1')}")
    print(f"  coefficients: {PHYSICAL_COST_TOML}")
    print(f"  feature contract: {FEATURE_CONTRACT_JSON}")
    for warn in warnings:
        print(f"  [warn] {warn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
