# Data quality summary

Generated from `reports/data_quality.json` after Role A ingestion. Prediction unit is the **batch instance**, not the machine-minute.

## Coverage

| Rack | Machines | Instance rows | Time-grid rows | Time span (s) | ~Days |
|------|----------|---------------|----------------|---------------|-------|
| Primary (domain 51) | 40 | 13,088,475 | 414,820 | 0 – 691,190 | 8.0 |
| Replication (domain 52) | 40 | 13,139,756 | 414,279 | 0 – 691,190 | 8.0 |

The second rack is a **replication** check, not an out-of-distribution test. Same 8-day window, same hardware spec, near-identical marginals (CPU 40.72 vs 40.23).

## Prediction target (instance-level)

| Rack | Trainable rows | Failed | Positive rate | Costing-eligible |
|------|----------------|--------|---------------|------------------|
| Primary | 12,974,617 | 21,780 | **0.1679%** | 4,924 |
| Replication | 13,027,395 | 22,087 | **0.1695%** | 5,123 |

Trainable = `Failed`/`Interrupted`/`Terminated`. Censored (still running at trace end): 113,858 primary / 112,361 replication.

Costing-eligible = failed **and** measured waste window ≥ 60 s **and** not imputed. Most failed instances have `end_time=0` (failure time unknown) or a sub-minute lifetime; those stay in the classifier set but are excluded from kWh/liter estimates. Median waste window across all instances is 10 s — the costing set is the long-running tail, which is the only place a kill-at-admission policy can save measurable energy.

Measured costing pool vs minimum waste window (primary / replication):

| Threshold | Primary | Replication |
|-----------|---------|-------------|
| ≥ 1 s | 14,585 | 14,882 |
| ≥ 10 s | 6,879 | 7,116 |
| ≥ 30 s | 5,258 | 5,447 |
| ≥ 60 s (default) | 4,924 | 5,123 |
| ≥ 120 s | 3,848 | 3,966 |
| ≥ 300 s | 2,389 | 2,455 |

Failed instances with `end_time=0` but a parent-task end: **5,303** primary / **5,321** replication. Those carry `waste_window_upper_bound_seconds` and `waste_window_imputed=1`. They are **not** in `eligible_for_costing`. The replication rack's 5,123 costing-eligible failures may be used as an extra costing pool only after primary-rack evaluation is frozen.

Always-predict-1 precision at this target is **0.17%**, not 37%. That is why the unit of analysis moved.

## Frozen split (primary)

`data/processed/primary_time_split.json` (714 bytes; timestamps are not enumerated):

| | Train (`start_time` < 518,355) | Test |
|--|-------------------------------|------|
| Instances | 9,083,115 | 4,005,360 |
| Positive rate (trainable) | **0.2000%** | **0.0951%** |
| Costing-eligible | 4,720 | 204 |

The failure rate drops in the last two days. Role B must not re-split randomly, and should treat that shift as a property of the test set rather than a bug.

## Failure status breakdown (`batch_instance`)

| Status | Primary | Replication |
|--------|---------|-------------|
| Terminated | 12,952,837 | 13,005,308 |
| Running | 113,814 | 112,319 |
| Failed | 20,693 | 21,124 |
| Interrupted | 1,087 | 963 |
| Ready | 44 | 42 |

Positive labels use **Failed + Interrupted** only.

## Auxiliary machine-minute column (not the target)

`failure_within_horizon` remains on `time_grid.parquet` for diagnostics. Positive rates: primary 36.84%, replication 37.59% at 30 min. **Do not train on it.** Feature contract denies `seconds_to_next_failure` and `failure_within_horizon`.

Horizon sensitivity of that auxiliary column (`python scripts/horizon_sensitivity.py`):

| Rack | 15 min | 30 min | 60 min |
|------|--------|--------|--------|
| Primary | 23.03% | 36.84% | 54.25% |
| Replication | 23.53% | 37.59% | 55.30% |

## Data gaps

`data_gap` rate is 0.00% on both racks. `mem_gps` / `mkpi` are ~78% null in raw `machine_usage` — missing sensors in the Alibaba trace, not a pipeline defect.

## CPU utilization (`machine_usage`)

| Rack | Mean | p50 | p95 |
|------|------|-----|-----|
| Primary | 40.72% | 40% | 66% |
| Replication | 40.23% | 39% | 65% |

Racks are homogeneous (96 cores, mem 100). Deltas: CPU 0.50 pp, memory ~0.02 pp, instance failure rate 0.0016 pp. This confirms replication rather than distribution shift.

## Raw table row counts

| Table | Primary | Replication |
|-------|---------|-------------|
| machine_usage | 2,470,351 | 2,467,918 |
| batch_instance | 13,088,475 | 13,139,756 |
| batch_task | 14,295,731 | 14,295,731 |
