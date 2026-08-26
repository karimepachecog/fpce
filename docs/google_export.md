# Google cluster-data 2019 export

The adapter `fpce-google-events` does **not** query BigQuery. It reads a local directory of parquet shards (or a single CSV/Parquet file) and writes one row per Borg **attempt**.

```bash
fpce-google-events --input data/raw/google
fpce-google-quality
fpce-cross-provider
```

A BigQuery export of a large result lands as many files (`extraccion_*.parquet`), not one. The shards are unordered: every file spans the full `collection_id` range, so events for the same instance are scattered. The ETL therefore cannot run shard-by-shard; DuckDB sorts out-of-core.

## Why attempts, not instances

Borg reschedules after `EVICT` / `KILL`. On a complete-group hash sample, 39.1% of `(collection_id, instance_index)` keys have more than one terminal event. Taking the first terminal versus the last swung the positive rate from 6.75% to 0.08%. The homolog of an Alibaba `batch_instance` (where `seq_no` is already a retry index) is a SCHEDULE paired with the **next** terminal. That pair is one attempt; `attempt_index` counts them.

## Expected columns (one row per Borg `instance_events` record)

| Column | Source | Notes |
|--------|--------|-------|
| `collection_id` | `collection_id` | Job/collection id |
| `instance_index` | `instance_index` | Instance within the collection |
| `time` | `time` | **Microseconds** from 600 s before trace start |
| `type` | `type` | Integer event code (see below) |
| `machine_id` | `machine_id` | Optional; **not** a cross-provider feature |
| `cpus_request` | `resource_request.cpus` | Flattened; fraction of the largest machine in the cell |
| `memory_request` | `resource_request.memory` | Flattened; same unit as CPU request |
| `priority` | `priority` | Optional |
| `scheduling_class` | `scheduling_class` | Optional |

2019 event codes used by the adapter:

| Code | Name | Role in FPCE |
|------|------|----------------|
| 0 | SUBMIT | Fallback start if no SCHEDULE (`start_imputed=1`) |
| 3 | SCHEDULE | Attempt start / `decision_time` |
| 4 | EVICT | `terminal_type=evicted`, **not** trained on |
| 5 | FAIL | `failed=1`, training positive |
| 6 | FINISH | `outcome=succeeded`, training negative |
| 7 | KILL | `terminal_type=killed`, **not** trained on |

## BigQuery (one cell, one week)

Requires a Google Cloud project with BigQuery enabled. The 2019 traces live in `google.com:google-cluster-data`. Prefer a **single cell** (`clusterdata_2019_a` … `_h`) and a **single week** — a full-month scan of all eight cells is 2.4 TiB and will run up a bill.

```sql
-- Cell A, first 7 days after the 600 s origin.
SELECT
  collection_id,
  instance_index,
  time,
  type,
  machine_id,
  resource_request.cpus   AS cpus_request,
  resource_request.memory AS memory_request,
  priority,
  scheduling_class
FROM `google.com:google-cluster-data.clusterdata_2019_a.instance_events`
WHERE time BETWEEN 600000000
              AND 600000000 + 7 * 24 * 3600 * 1000000
  AND type IN (0, 3, 4, 5, 6, 7)
```

In the BigQuery UI: set destination to a GCS bucket or download the result, then copy the parquet shards into `data/raw/google/`. If the export stores seconds instead of microseconds, pass `--time-unit s`.

## Handoff

Role A (data engineer), in order, after the shards are in `data/raw/google/`:

```bash
# DuckDB out-of-core. Measured ~4.5 min for 211M events → 67.9M attempts
# with --memory-limit 2GB; spill goes to data/interim/duckdb_tmp.
fpce-google-events --input data/raw/google --memory-limit 2GB
fpce-google-quality

# Laptop sample from an existing attempts table (does not re-run the ETL):
fpce-google-events --sample-from data/processed/google/attempts.parquet
```

Outputs:

- `data/processed/google/attempts.parquet` — one row per attempt
- `data/processed/google/attempts_sample.parquet` — ~1M FAIL+FINISH rows (~80 MB, same ~18.26% rate). Default `--google` path for `fpce-cross-provider` on a laptop.
- `data/processed/google/export_manifest.json` — shard counts, checksums, terminal tallies
- `reports/google_quality.json` — prevalence, multi-attempt rate, costing-pool curve

Role B (data scientist) owns the official classifier. Role A ran `fpce-cross-provider` as an **adapter smoke-test** (HistGB on overlapping `plan_cpu_frac` / `plan_mem_frac` / `retry_index`, Google laptop sample, `max_train_rows=200000`). Metrics in `reports/cross_provider.json` as they came out:

| Split | n | Pos. rate | ROC-AUC | PR-AUC | Lift |
|-------|---|-----------|---------|--------|------|
| Alibaba time-test | 3,974,412 | 0.0951% | 0.6107 | 0.0991 | 104.2 |
| Google sample | 1,000,111 | 18.28% | 0.5095 | 0.2101 | 1.15 |
| Google equalized prevalence | 898,158 | 9.00% | 0.5092 | 0.1128 | 1.25 |

ROC-AUC drop Alibaba→Google: **0.101**. F1 at 0.5 on Google (0.37) is confounded by the prevalence gap; do not cite it as shift. This helper is not Role B's full model. Role B may re-run on the full `attempts.parquet` (16+ GB RAM).

```bash
fpce-cross-provider                          # uses attempts_sample.parquet if present
fpce-cross-provider --google data/processed/google/attempts.parquet   # full table, 16+ GB RAM
```

- Trains on Alibaba (`data/processed/primary/instance_events.parquet`, time split) and scores on Google attempts.
- Features: overlapping allow-list after unit alignment — `plan_cpu_frac`, `plan_mem_frac`, `retry_index` (`seq_no` / `attempt_index`). Never `machine_id`, never raw `plan_cpu`.
- Headline metrics in `reports/cross_provider.json` are ROC-AUC, PR-AUC, and lift = PR-AUC / base rate. F1 at threshold 0.5 is labelled as confounded by the ~109× prevalence gap. An equalized-prevalence variant downsamples Google positives to the Alibaba rate.

Role C (electrical engineer): Google waste windows are wall-clock seconds of a Borg attempt. They are physically comparable to Alibaba waste windows only after scaling by machine capacity (Google requests are already a fraction of the largest machine; Alibaba `plan_cpu / 100 / 96` is the matching CPU fraction). Do not multiply Google durations by Alibaba watt envelopes without that scaling.
