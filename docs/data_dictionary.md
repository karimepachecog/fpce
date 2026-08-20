# Data dictionary

Two tables. **`instance_events.parquet` is the prediction unit.** `time_grid.parquet` is a feature source (host state at decision time), not the label table.

## `instance_events.parquet` (Role B target)

One row per batch instance on the rack.

**Paths:**
- Training rack: `data/processed/primary/instance_events.parquet`
- Replication rack: `data/processed/replication/instance_events.parquet`

| Column | Type | Units | Description |
|--------|------|-------|-------------|
| `instance_name` | string | — | Trace instance id. **Denied as a feature.** |
| `task_name`, `job_name` | string | — | Parent task/job. **Denied as features** (identity leakage across siblings). |
| `task_type` | int | — | Alibaba task type. Allowed. |
| `machine_id` | string | — | Host at admission. Allowed (join key to time grid). |
| `seq_no`, `total_seq_no` | int | count | Retry index / planned retries. Allowed. |
| `plan_cpu`, `plan_mem`, `instance_num` | float | plan units | From `batch_task`. Allowed. Known at admission. |
| `start_time` | int | seconds | Trace-relative start. Split key; not a model feature. |
| `end_time` | int | seconds | Recorded end; 0 if missing. **Denied.** |
| `status` | string | — | Raw outcome. **Denied.** |
| `cpu_avg`, `cpu_max`, `mem_avg`, `mem_max` | float | — | Instance telemetry **after completion**. **Denied.** |
| `failed` | int | 0/1 | **Label:** 1 if `Failed` or `Interrupted`. |
| `outcome` | string | — | `failed` / `succeeded` / `censored` / `other`. **Denied.** |
| `decision_time` | int | seconds | `start_time + DECISION_OFFSET_SECONDS` (currently 0 = at admission). |
| `event_end` | float | seconds | Outcome timestamp: `end_time` if > 0, else `start_time` for failed rows with `end_time=0`. NA if censored. |
| `waste_window_seconds` | float | seconds | `event_end - decision_time`, clipped at 0. **Denied** (derived from outcome). |
| `eligible_for_training` | int | 0/1 | 1 if outcome is failed or succeeded (not still-running). |
| `eligible_for_costing` | int | 0/1 | 1 if failed **and** waste window ≥ 60 s (enough duration to estimate wasted energy). |

Machine-readable allow/deny list: `params/feature_contract.json`, loaded via `fpce.contracts.load_feature_contract()`.

## `time_grid.parquet` (feature source)

One row per `(machine_id, time_stamp)` at **1-minute** resolution. Join to instances on `machine_id` with `time_stamp <= decision_time` only.

**Paths:**
- Training rack: `data/processed/primary/time_grid.parquet`
- Replication rack: `data/processed/replication/time_grid.parquet`

| Column | Type | Units | Description |
|--------|------|-------|-------------|
| `machine_id` | string | — | Alibaba machine uid (e.g. `m_1486`) |
| `time_stamp` | int | seconds | Seconds from trace start |
| `cpu_util_percent` | float | 0–100 | Mean CPU utilization in this minute |
| `mem_util_percent` | float | 0–100 | Mean memory utilization |
| `mem_gps` | float | 0–100 | Mean memory bandwidth (normalized) |
| `mkpi` | float | 0–100 | Memory KPI |
| `net_in` / `net_out` | float | 0–100 | Normalized network traffic |
| `disk_io_percent` | float | 0–100 | Disk I/O utilization |
| `active_instances` | int | count | Batch instances active on this machine |
| `seconds_to_next_failure` | float | seconds | **Denied.** Auxiliary; not a feature. |
| `failure_within_horizon` | int | 0/1 | **Denied.** Legacy machine-minute label (~37% positive). Not the prediction target. |
| `data_gap` | int | 0/1 | 1 if all utilization columns are null |

## Why the unit of analysis changed

Each machine sees ~550 instance failures over 8 days (~one per 21 minutes). A 30-minute machine-minute horizon therefore has a ~37% positive rate — a constant classifier already matches that precision. Instance-level `Failed`/`Interrupted` is ~0.17% of completed instances, and killing a doomed instance at admission is an actionable counterfactual.

## Null semantics

- Utilization nulls (~78% on `mem_gps`/`mkpi`) reflect missing sensors in the trace
- `event_end` is NA on censored (still-running) instances
- Failed rows with `end_time=0` keep `start_time` as the failure proxy and are **not** costable (zero waste window)

## Split

Time-based, frozen in `data/processed/primary_time_split.json`: instances with `start_time < split_timestamp` are train.
