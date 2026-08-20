#!/usr/bin/env bash
# Verify batch_instance.tar.gz is complete before running filter/build stages.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCHIVE="$ROOT/data/raw/batch_instance.tar.gz"
EXPECTED_SIZE=21204654955
EXPECTED_SHA="e73e5a9326669aa079ba20048ddd759383cabe1fe3e58620aa75bd034e2450c6"

if [[ ! -f "$ARCHIVE" ]]; then
  echo "[blocked] Missing $ARCHIVE"
  exit 1
fi

ACTUAL_SIZE=$(stat -c%s "$ARCHIVE")
if [[ "$ACTUAL_SIZE" -ne "$EXPECTED_SIZE" ]]; then
  echo "[blocked] Incomplete download: $ACTUAL_SIZE / $EXPECTED_SIZE bytes"
  exit 1
fi

ACTUAL_SHA=$(sha256sum "$ARCHIVE" | awk '{print $1}')
if [[ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]]; then
  echo "[blocked] Checksum mismatch: got $ACTUAL_SHA"
  exit 1
fi

echo "[ok] batch_instance.tar.gz verified ($ACTUAL_SIZE bytes)"
