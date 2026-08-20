#!/usr/bin/env bash
# Build time_grid.parquet one machine at a time to stay within RAM limits.
set -euo pipefail

RACK="${1:-primary}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IDS_JSON="$ROOT/data/processed/rack_machine_ids.json"
CHUNK_DIR="$ROOT/data/processed/primary/time_grid_chunks"
OUT="$ROOT/data/processed/primary/time_grid.parquet"

if [[ "$RACK" == "replication" ]]; then
  IDS_JSON="$ROOT/data/processed/replication_rack_machine_ids.json"
  CHUNK_DIR="$ROOT/data/processed/replication/time_grid_chunks"
  OUT="$ROOT/data/processed/replication/time_grid.parquet"
fi

mkdir -p "$CHUNK_DIR"

mapfile -t MACHINE_IDS < <(
  python3 -c "import json; print('\n'.join(json.load(open('$IDS_JSON'))['machine_ids']))"
)

echo "== Step 5 (chunked): rack=$RACK machines=${#MACHINE_IDS[@]} =="
for mid in "${MACHINE_IDS[@]}"; do
  chunk="$CHUNK_DIR/${mid}.parquet"
  if [[ -f "$chunk" ]]; then
    echo "[skip] $mid (chunk exists)"
    continue
  fi
  echo "[build] $mid"
  fpce-time-grid --rack "$RACK" --machine-id "$mid" --chunk-dir "$CHUNK_DIR"
done

echo "== Merging chunks -> $OUT =="
fpce-time-grid --rack "$RACK" --merge-chunks "$CHUNK_DIR" --output "$OUT"
echo "[ok] Step 5 complete for rack=$RACK"
