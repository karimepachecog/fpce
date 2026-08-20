# Role handoff contracts

## Role A → Role B (data scientist)

| Deliverable | Path | Notes |
|-------------|------|-------|
| Instance events (target) | `data/processed/primary/instance_events.parquet` | One row per batch instance; label = `failed` |
| Feature contract | `params/feature_contract.json` | Allow/deny lists; `fpce.contracts.assert_no_leakage` |
| Host time grid | `data/processed/primary/time_grid.parquet` | Join at `time_stamp <= decision_time` only |
| Frozen split | `data/processed/primary_time_split.json` | Time-based on `start_time`; do not re-split randomly |
| Replication events | `data/processed/replication/instance_events.parquet` | Hold out until the final replication check |
| Quality report | `reports/data_quality.json`, `reports/data_quality.md` | Coverage, class balance, instance rates |

**Role B must not:** change label definitions, train on denied columns, re-split randomly, or touch the replication rack during training.

**Naming caveat:** the second rack is a *replication* check, not an out-of-distribution test. Same 8-day window, same hardware spec, near-identical marginals. Do not report it as generalization under distribution shift.

## Role B → Role C (electrical engineer)

| Deliverable | Format | Notes |
|-------------|--------|-------|
| True-positive doomed instances | JSON/Parquet | `instance_name`, `machine_id`, `decision_time`, `event_end`, `waste_window_seconds` |
| Utilization during waste window | From time_grid | Host CPU during `[decision_time, event_end)` for costing-eligible TPs |
| Reactive baseline fire time | per TP | For lead-time vs a retry-count / runtime-threshold rule |

Costing uses only rows with `eligible_for_costing=1` (failed and waste window ≥ 60 s).

## Role A → Role C (direct)

| Deliverable | Path | Notes |
|-------------|------|-------|
| Coefficient registry | `params/physical_cost.toml` | P_idle, P_peak, PUE, WUE |
| Loader | `fpce.costing.coefficients` | `load_physical_cost_params()`, `sweep()` |
| Translation identity | see `docs/coefficients.md` | Water = IT kWh × WUE. Facility energy = IT kWh × PUE. No cooling share. |

## Role B + C → Role D (software engineer)

| Deliverable | Source | Notes |
|-------------|--------|-------|
| Trained classifier | Role B | Inference at instance `decision_time` |
| Threshold baseline | Role B | Reactive comparison per event |
| Cost translation | Role C | kWh/liter range per waste window |
| Time grid + instance events | Role A | Replay source |

**Role D owns:** wiring B and C together in the replay harness, logging lead times, and reporting accumulated physical-cost ranges across true-positive doomed instances (proposal Methodology §4).

## Replay harness (Role D)

```bash
fpce-replay --rack primary --output replay.jsonl --limit 100
```

Streams `time_grid.parquet` as simulated real-time JSONL. Role D extends this so that, when an instance reaches `decision_time`, the classifier and (on TP) the cost translator run.

## What is NOT a downloadable dataset

Fan et al. (2007), LBNL 2024 PUE/WUE, and Green Grid WP#35 are **published coefficients**, not telemetry. They live in `params/physical_cost.toml`. No joint fault-and-water ground truth exists in public data.
