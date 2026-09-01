"""CLI entry point for Role C costing."""

import argparse
import datetime
import hashlib
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from fpce.config import PROJECT_ROOT, PHYSICAL_COST_TOML, DATA_PROCESSED, REPORTS_DIR
from fpce.costing.coefficients import load_physical_cost_params
from fpce.costing.translate import translate

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Role C physical cost estimator.")
    parser.add_argument(
        "--handoff",
        type=Path,
        default=REPORTS_DIR / "role_b_handoff.parquet",
        help="Path to Role B handoff parquet",
    )
    parser.add_argument(
        "--grid",
        type=Path,
        default=DATA_PROCESSED / "primary" / "time_grid.parquet",
        help="Path to host time grid parquet",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPORTS_DIR / "role_c_costing.parquet",
        help="Output parquet path",
    )
    parser.add_argument(
        "--params",
        type=Path,
        default=PHYSICAL_COST_TOML,
        help="Path to physical_cost.toml",
    )

    args = parser.parse_args()

    if not args.handoff.exists():
        logging.error(f"Handoff file not found: {args.handoff}")
        return 1

    if not args.grid.exists():
        logging.error(f"Grid file not found: {args.grid}")
        return 1

    logging.info(f"Loading params from {args.params}")
    params_registry = load_physical_cost_params(args.params)
    corners = params_registry.sweep()
    logging.info(f"Evaluated {len(corners)} physical cost corners.")

    logging.info(f"Reading handoff from {args.handoff}")
    df_handoff = pd.read_parquet(args.handoff)

    if "eligible_for_costing" not in df_handoff.columns:
        logging.error("Column 'eligible_for_costing' missing in handoff.")
        return 1

    df_eligible = df_handoff[df_handoff["eligible_for_costing"] == 1].copy()
    num_eligible = len(df_eligible)
    logging.info(f"Filtered {num_eligible} eligible rows for costing.")

    if num_eligible != 204:
        logging.warning(f"Expected exactly 204 eligible rows, but found {num_eligible}.")

    if num_eligible == 0:
        logging.info("No eligible rows to process. Exiting early.")
        return 0

    logging.info(f"Reading time grid from {args.grid}")
    df_grid = pd.read_parquet(args.grid, columns=["machine_id", "time_stamp", "cpu_util_percent"])

    results = []

    logging.info("Processing instances...")
    for idx, row in df_eligible.iterrows():
        test_row_index = row.get("test_row_index", idx)
        instance_name = row["instance_name"]
        machine_id = row["machine_id"]
        decision_time = float(row["decision_time"])
        event_end = float(row["event_end"])

        # Fetch grid for this machine and time window [decision_time, event_end)
        mask = (
            (df_grid["machine_id"] == machine_id) &
            (df_grid["time_stamp"] >= decision_time) &
            (df_grid["time_stamp"] < event_end)
        )
        grid_slice = df_grid[mask].sort_values("time_stamp")

        util_percent = grid_slice["cpu_util_percent"].to_numpy()
        util_fraction = util_percent / 100.0

        dt_seconds = event_end - decision_time

        # Calculate cost for all corners
        corner_results = []
        for corner in corners:
            res = translate(util_fraction, dt_seconds, corner)
            corner_results.append(res)
        
        # Aggregate
        if corner_results:
            it_kwh = [r.it_kwh for r in corner_results]
            fac_kwh = [r.facility_kwh for r in corner_results]
            water = [r.water_liters for r in corner_results]

            results.append({
                "test_row_index": test_row_index,
                "instance_name": instance_name,
                "machine_id": machine_id,
                "decision_time": decision_time,
                "event_end": event_end,
                "u_mean": corner_results[0].u_mean,
                "dt_covered_seconds": corner_results[0].dt_covered_seconds,
                "it_kwh_min": min(it_kwh),
                "it_kwh_max": max(it_kwh),
                "facility_kwh_min": min(fac_kwh),
                "facility_kwh_max": max(fac_kwh),
                "water_liters_min": min(water),
                "water_liters_max": max(water),
            })

    df_out = pd.DataFrame(results)
    
    out_path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write parquet
    table = pa.Table.from_pandas(df_out, preserve_index=False)
    pq.write_table(table, out_path)
    logging.info(f"Wrote output parquet to {out_path}")

    # Calculate SHA256 of the parquet file
    hasher = hashlib.sha256()
    with open(out_path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    file_hash = hasher.hexdigest()

    manifest_path = out_path.with_name(out_path.stem + "_manifest.json")
    try:
        rel_out = out_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        rel_out = out_path.name
        
    try:
        rel_params = args.params.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        rel_params = args.params.name

    manifest = {
        "role": "C",
        "output_parquet": rel_out,
        "sha256": file_hash,
        "row_count": len(df_out),
        "eligible_for_costing_count": num_eligible,
        "corners_evaluated": len(corners),
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "params_toml": rel_params,
    }

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    logging.info(f"Wrote manifest to {manifest_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
