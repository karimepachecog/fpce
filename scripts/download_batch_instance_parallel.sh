#!/usr/bin/env bash
# Parallel range download for batch_instance.tar.gz
set -euo pipefail

cd "$(dirname "$0")/.."
URL="http://clusterdata2018pubcn.oss-cn-beijing.aliyuncs.com/batch_instance.tar.gz"
HOST="clusterdata2018pubcn.oss-cn-beijing.aliyuncs.com"
OUT="data/raw/batch_instance.tar.gz"
SIZE=21204654955
PARTS=8
PART_SIZE=$((SIZE / PARTS))
EXPECTED_SHA="e73e5a9326669aa079ba20048ddd759383cabe1fe3e58620aa75bd034e2450c6"
LOG_DIR="data/raw/batch_instance_parts"

resolve_ipv4() {
  local hostname="$1"
  local ip
  while IFS= read -r ip; do
    ip="${ip%.}"
    if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      echo "$ip"
      return 0
    fi
  done < <(dig @8.8.8.8 +short "$hostname" A)
  echo "[error] Could not resolve IPv4 for $hostname" >&2
  return 1
}

IP="$(resolve_ipv4 "$HOST")"
mkdir -p data/raw "$LOG_DIR"

# Keep partial parts; only remove merged output if we are rebuilding from complete parts.
if [[ -f "$OUT" ]]; then
  CURRENT_SIZE=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
  if [[ "$CURRENT_SIZE" -lt "$SIZE" ]]; then
    echo "[info] Keeping partial $OUT ($CURRENT_SIZE bytes); parts will be merged when complete"
  fi
fi

echo "[download] Starting $PARTS-part parallel download ($SIZE bytes) via $IP"
for i in $(seq 0 $((PARTS - 1))); do
  START=$((i * PART_SIZE))
  if [ "$i" -eq $((PARTS - 1)) ]; then
    END=$((SIZE - 1))
  else
    END=$((START + PART_SIZE - 1))
  fi
  PART_FILE="data/raw/batch_instance.part.${i}"
  PART_LOG="$LOG_DIR/part_${i}.log"
  (
    {
      echo "[start] part $i range ${START}-${END}"
      curl -C - -L --fail --retry 5 --retry-delay 3 --connect-timeout 30 \
        --resolve "${HOST}:80:${IP}" \
        -r "${START}-${END}" \
        -o "$PART_FILE" \
        "$URL"
      echo "[ok] part $i complete"
    } >>"$PART_LOG" 2>&1
  ) &
done
wait

echo "[merge] Combining parts..."
cat data/raw/batch_instance.part.* > "$OUT.new"
mv "$OUT.new" "$OUT"
rm -f data/raw/batch_instance.part.*

ACTUAL_SHA=$(sha256sum "$OUT" | awk '{print $1}')
if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
  echo "[error] Checksum mismatch: expected $EXPECTED_SHA got $ACTUAL_SHA"
  exit 1
fi
echo "[ok] batch_instance downloaded and verified ($(du -h "$OUT" | cut -f1))"
