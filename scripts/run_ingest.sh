#!/usr/bin/env bash
# End-to-end Alibaba trace ingestion for FPCE MVP (Role A).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== Step 1: Download trace files (skip if checksum-verified) =="
fpce-download --files machine_meta machine_usage batch_task --extract
fpce-download --files batch_instance

echo "== Step 2: Select racks =="
# Primary rack already in data/processed/rack_machine_ids.json (domain 51)
fpce-select-rack \
  --exclude-domain 51 \
  --output "$ROOT/data/processed/replication_rack_machine_ids.json" \
  --machines-parquet "$ROOT/data/processed/replication_rack_machines.parquet"

echo "== Step 3: Filter batch_instance for both racks (single pass) =="
bash "$ROOT/scripts/check_download.sh"
fpce-filter-instances \
  --source "$ROOT/data/raw/batch_instance.tar.gz" \
  --rack-ids "$ROOT/data/processed/rack_machine_ids.json" \
  --rack-ids "$ROOT/data/processed/replication_rack_machine_ids.json" \
  --dest-dir "$ROOT/data/interim"

echo "== Step 4: Build parquet tables per rack =="
fpce-build-parquet --rack primary --rack replication

echo "== Step 5: Build 1-min time grids (chunked, low memory) =="
bash "$ROOT/scripts/run_time_grid_chunked.sh" primary
bash "$ROOT/scripts/run_time_grid_chunked.sh" replication

echo "== Step 6: Instance-level events (prediction unit) =="
fpce-instance-events --rack primary
fpce-instance-events --rack replication

echo "== Step 7: Quality reports and frozen split =="
fpce-quality-report
fpce-freeze-split --rack primary

echo "[ok] Role A ingestion complete"
