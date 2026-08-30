# Role handoff contracts

## Role A → Role B (data scientist)

| Deliverable | Path | Notes |
|-------------|------|-------|
| Instance events (target) | `data/processed/primary/instance_events.parquet` | One row per batch instance; label = `failed` |
| Feature contract | `params/feature_contract.json` | Allow/deny lists; `fpce.contracts.assert_no_leakage` |
| Host time grid | `data/processed/primary/time_grid.parquet` | Join with `fpce.features.windows.join_host_at_decision` (`time_stamp <= decision_time`) |
| Frozen split | `data/processed/primary_time_split.json` | Time-based on `start_time`; do not re-split randomly |
| Replication events | `data/processed/replication/instance_events.parquet` | Hold out until the final replication check |
| Quality report | `reports/data_quality.json`, `reports/data_quality.md` | Coverage, class balance, instance rates |
| Google attempts (cross-provider) | `data/processed/google/attempts.parquet` | One row per Borg attempt, not per instance. Score with `fpce-cross-provider`; use `plan_cpu_frac` / `plan_mem_frac`. Full table needs 16+ GB RAM. |
| Google laptop sample | `data/processed/google/attempts_sample.parquet` | ~1M stratified FAIL+FINISH rows. Default `--google` path for `fpce-cross-provider` when present. |
| USB checksums | `data/processed/HANDOFF.md` | SHA256 of the processed kit. Overlay USB onto `data/processed/` after clone. |
| Google quality | `reports/google_quality.json` | Prevalence, multi-attempt rate, costing-pool curve |

**Role B must not:** change label definitions, train on denied columns, re-split randomly, or touch the replication rack during training.

**Naming caveat:** the second rack is a *replication* check, not an out-of-distribution test. Same 8-day window, same hardware spec, near-identical marginals. Do not report it as generalization under distribution shift.

## Role B → Role C (electrical engineer)

Role B is **frozen**. Full contract: [role_b.md](role_b.md). **Do not compute kWh or liters until Role C starts.**

| Deliverable | Path | Notes |
|-------------|------|-------|
| Per test-event scores and alerts | `reports/role_b_handoff.parquet` | One row per eligible frozen-test instance. Manifest: `reports/role_b_handoff_manifest.json` |
| Official classifier | `models/primary_hgb_frozen.joblib` | HistGB, threshold 0.9 |
| Lead-time summary | `reports/primary_hgb_lead_time.json` | 1,210 anticipated; median 17 s |

Filter `eligible_for_costing=1` (failed, **measured** waste window ≥ 60 s, not imputed). Join host CPU from `data/processed/primary/time_grid.parquet` on `[decision_time, event_end)`. Map back to `instance_events.parquet` with `instance_name`, `machine_id`, `start_time`, `seq_no`, `decision_time`, `test_row_index`.

`waste_window_upper_bound_seconds` is a parent-task bound for rows with `end_time=0`; it is never mixed into the measured window.

The replication rack (5,123 costing-eligible failures) may be used as an **additional costing pool only after** primary-rack evaluation is frozen. It is not extra training data.

## Role A → Role C (direct)

| Deliverable | Path | Notes |
|-------------|------|-------|
| Coefficient registry | `params/physical_cost.toml` | P_idle, P_peak, LBNL PUE/WUE, plus `[[operators]]` ESG point values |
| Loader | `fpce.costing.coefficients` | `load_physical_cost_params()`, `sweep()`, `load_operator_profiles()`, `operator_scale_vs_national()` |
| Operator scale (no Fan) | `reports/operator_coefficient_scale.json` | How water/facility kWh would scale vs LBNL for the same IT kWh. Not a kWh result. |
| Translation identity | see `docs/coefficients.md` | Water = IT kWh × WUE. Facility energy = IT kWh × PUE. No cooling share. |

## Role B + C → Role D (software engineer)

| Deliverable | Source | Notes |
|-------------|--------|-------|
| Trained classifier | `models/primary_hgb_frozen.joblib` | Score at `decision_time`; threshold 0.9. See `fpce.model.freeze.predict_proba_with_bundle` |
| Frozen-test scores | `reports/role_b_handoff.parquet` | Includes `model_alert`, reactive times, lead times |
| Reactive baseline | `src/fpce/model/baseline.py` | Train medians in `reports/primary_hgb_lead_time.json` |
| Cost translation | Role C | Not implemented yet (kWh/liter ranges) |
| Time grid + instance events | Role A | Replay source |

**Role D owns:** wiring B and C together in the replay harness, logging lead times, and reporting accumulated physical-cost ranges across true-positive doomed instances (proposal Methodology §4).

## Replay harness (Role D)

```bash
fpce-replay --rack primary --output replay.jsonl --limit 100
```

Streams `time_grid.parquet` as simulated real-time JSONL. Role D extends this so that, when an instance reaches `decision_time`, the classifier and (on TP) the cost translator run.

## After Role B freezes primary-rack evaluation (not Role A)

These analyses are in scope for B/C/D. Role A already tagged the rows and cited the coefficients; it does **not** implement `translate.py`, the official classifier, or the policy simulator.

| Item | Owner | Notes |
|------|-------|-------|
| Cost the replication rack (5,123 `eligible_for_costing` failures) | C, after B freeze | Extra costing pool, not training data. Combine with the primary 4,924 / test 204. |
| Cost Google's 1.18M attempts with `eligible_for_costing=1` | C | Secondary/exploratory. Scale waste windows by machine-fraction capacity; do not apply Alibaba watt envelopes to Google durations raw. |
| Scheduler policy simulation (kill if P(fail) > threshold vs reactive baseline) | B + C + D | Needs the official classifier, Fan integral, and replay. |
| AI-compute governance prototype on Supercloud | Future Work | Out of MVP. A only downloaded GPU `dcgm.csv`; the cited paper uses power and network. |

`fpce-cross-provider` is a **Role A adapter smoke-test** (HistGB on overlapping `*_frac` columns). Role B still owns the full classifier, baseline, and lead-time events.

## What is NOT a downloadable dataset

Fan et al. (2007), LBNL 2024 PUE/WUE, operator ESG point values, and Green Grid WP#35 are **published coefficients**, not telemetry. They live in `params/physical_cost.toml`. No joint fault-and-water ground truth exists in public data.
