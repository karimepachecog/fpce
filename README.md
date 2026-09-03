# FPCE Datacenter Project

Fault-triggered Physical Cost Estimator (FPCE) — predicting doomed batch instances on the Alibaba cluster trace and translating their remaining runtime into estimated physical cost (kWh and liters of cooling water).

## Role map

| Role | Discipline | Owns | Key outputs |
|------|------------|------|-------------|
| **A — Data engineer** | Pipeline & data | Trace ingestion, instance labels, coefficient registry | `instance_events.parquet`, `time_grid.parquet`, `params/physical_cost.toml` |
| **B — Data scientist** | ML | Features, classifier, reactive baseline, lead-time events | **Frozen.** HistGB t=0.9: `models/primary_hgb_frozen.joblib`, `reports/role_b_handoff.parquet`, [docs/role_b.md](docs/role_b.md) |
| **C — Electrical engineer** | Physical cost | Fan et al. power model + PUE + WUE | kWh/liter **ranges** on the 204 costing-eligible test failures (`reports/role_c_costing.parquet`) |
| **D — Software engineer** | Integration | Replay harness + policy sim | Accumulated ranges vs reactive baseline (`reports/policy_simulation.json`) |

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

python scripts/validate_config.py
pytest

# Full ingestion (requires ~25 GB for batch_instance)
bash scripts/run_ingest.sh
```

## Repository layout

```
params/physical_cost.toml     # P_idle, P_peak, PUE, WUE (cited ranges)
params/feature_contract.json  # Allow/deny lists (anti-leakage)
src/fpce/
  config.py                   # Paths, schemas, label constants
  contracts.py                # Feature-contract loader
  io.py                       # CSV/Parquet helpers
  ingest/                     # Role A pipeline
  costing/                    # Coefficient loader + Role C contract
  features/                   # Role B feature join (assemble + windows)
  model/                      # Role B classifier (HistGB frozen; XGB experiments)
  replay/                     # Role D contract
  provenance/                 # SPEC Power envelope scraper
data/processed/               # Parquets + split JSON (parquets gitignored)
reports/                      # data_quality.json + data_quality.md
docs/
```

## Google 2019 data format

The export in `data/raw/google/` is **many parquet files, not one**. BigQuery writes a large result as shards (`extraccion_000000000000.parquet`, …). Each file is a slice of rows, not a complete job, and the slices are **not sorted** — events for the same instance are scattered across shards.

Each row is a **lifecycle event** (submitted, scheduled, failed, finished, evicted, killed), not a task. A single Borg instance is typically several events, and after an eviction Borg often schedules it again. That second schedule is a new *attempt*, analogous to a new Alibaba `batch_instance` with a higher `seq_no`.

`fpce-google-events` is the ETL that groups events into attempts. It reads the whole directory with DuckDB (the table does not fit in RAM), writes `data/processed/google/attempts.parquet`, and a small `export_manifest.json`. Then `fpce-google-quality` and `fpce-cross-provider` consume that table.

| Column in the export | Meaning |
|----------------------|---------|
| `collection_id`, `instance_index` | Which Borg instance |
| `time` | Microseconds from 600 s before trace start |
| `type` | Event code: 0 submit, 3 schedule, 4 evict, 5 fail, 6 finish, 7 kill |
| `cpus_request`, `memory_request` | Requested resources, as a **fraction of the largest machine in the cell** (not cores) |
| `machine_id`, `priority`, `scheduling_class` | Optional context |

Do not train on `plan_cpu` / `plan_mem` mixed across providers. Use `plan_cpu_frac` / `plan_mem_frac`. Alibaba `plan_cpu` is hundredths of a core on a 96-thread machine; divide by 9,600 to get the same unit as Google. `machine_id` is not comparable across providers.

## Data for other roles (GitHub + USB)

GitHub carries **code, docs, and small JSON** (split, rack ids, quality reports, SPEC envelope 142 KB). The prediction tables are too large for GitHub and stay out of git.

Role A hands the processed tables on a USB stick (~**4.8 GB**, including the 81 MB Google laptop sample). **8 GB** is enough; **16 GB** is comfortable. Do not copy `data/raw/` (35 GB) or `data/interim/`.

After `git clone` and `pip install -e ".[dev]"`:

1. Copy the USB contents onto `data/processed/` (keep the same folder names).
2. Check files against [data/processed/HANDOFF.md](data/processed/HANDOFF.md) (SHA256 + sizes).

USB kit:

```
data/processed/primary/instance_events.parquet
data/processed/primary/time_grid.parquet
data/processed/replication/instance_events.parquet
data/processed/replication/time_grid.parquet
data/processed/google/attempts.parquet
data/processed/google/attempts_sample.parquet
data/processed/google/export_manifest.json
data/processed/spec_power_curves.parquet
data/processed/primary_time_split.json
data/processed/rack_machine_ids.json
data/processed/replication_rack_machine_ids.json
```

Skip `batch_instance.parquet`, `batch_task.parquet`, `machine_usage.parquet`, and `time_grid_chunks/` — Role B/C/D do not open them.

## What Role B (data scientist) produced (frozen)

Canonical document: [docs/role_b.md](docs/role_b.md).

- **Official model:** HistGradientBoosting, threshold **0.9** — `models/primary_hgb_frozen.joblib`
- **Handoff table (Roles C/D):** `reports/role_b_handoff.parquet` (one row per eligible frozen-test event)
- **Do not train on** `failure_within_horizon`. Target is `failed` on `eligible_for_training=1`.
- XGBoost v1/v2 reports exist under `reports/primary_xgb_*` but are **not** the official model.

Inputs used: `data/processed/primary/instance_events.parquet`, `time_grid.parquet`, `primary_time_split.json`, `params/feature_contract.json`.

## What Role C (electrical engineer) produced

`fpce-role-c-cost` runs Fan et al. over 16 coefficient corners on `eligible_for_costing=1`. Output: `reports/role_c_costing.parquet` (204 primary-test rows) and `reports/role_c_costing_manifest.json`. Water = IT kWh × WUE. Facility energy = IT kWh × PUE. No cooling share.

Replication rack (domain 52, costing only): **5,123** failures → **81–244 IT kWh**, 93–342 facility kWh, **37–117 L** (`reports/replication_eval.json`). The official HistGB pickle was not scored there (sklearn 1.4.2 vs this env). Google attempt costing is still out of MVP.

## What Role D (software engineer) produced

**Headline:** both admission-kill policies are **net-negative** on the 204-row costing pool. The model is **~100× less bad** than the reactive baseline because it fires on 0.6% of test rows (23,883 alerts), not 46.5% (1,849,651).

- `fpce-policy-sim` + `fpce-policy-report` write `reports/policy_simulation.json` and `reports/figures/policy_simulation.png` (avoided vs destroyed, log scale).
- Doomed-pool waste if nobody kills: **3.39–9.97 IT kWh**. Model t=0.9 avoids 3.32–9.78 but **destroys 60–179 IT kWh** of healthy work (20,523 FPs). Net **−176 to −51 IT kWh**.
- Baseline avoids 3.29–9.68 and **destroys 5,962–17,533 IT kWh** (1,848,844 FPs, 50k sample × 37). Net **−17,530 to −5,952 IT kWh**.
- The 203/204 “baseline catch rate” is a **pool artifact**: costing rows last ≥ 60 s and the baseline fires at ~10 s, so it almost always arrives. It is not a strong detector (46% of the whole test is an alert).
- Break-even vs costing TPs at t=0.9 needs **~15% precision** (observed **~1%**). A post-hoc sweep (`reports/policy_threshold_sweep.json`) goes net-positive at t=0.999 (196 TPs, 3 FPs) — that cut is **not** the official Role B threshold.
- `src/fpce/replay/runner.py` has an indent bug (`cpu_util_percent` conversion sits outside the row loop), so `replay_summary.json` `n_costed_rows=1` is not a finished experiment. Use `fpce-policy-sim`.

## Documentation

- [docs/handover.md](docs/handover.md) — one-page Role A handover (script order, USB, what B/C/D do next)
- [docs/role_b.md](docs/role_b.md) — **Role B freeze** (model, threshold, metrics, lead time, C/D handoff)
- [docs/pipeline.md](docs/pipeline.md) — stage-by-stage ingestion
- [docs/roles.md](docs/roles.md) — handoff contracts
- [docs/data_dictionary.md](docs/data_dictionary.md) — instance events + time grid
- [docs/coefficients.md](docs/coefficients.md) — physical cost identities and provenance
- [docs/google_export.md](docs/google_export.md) — BigQuery export schema and Role A/B handoff for Google 2019
- [reports/data_quality.md](reports/data_quality.md) — measured dataset summary
- [reports/policy_simulation.json](reports/policy_simulation.json) — avoided, destroyed, and net kWh/L (204-row pool + FP collateral)
- [reports/policy_threshold_sweep.json](reports/policy_threshold_sweep.json) — net vs threshold; break-even precision
- [reports/figures/policy_simulation.png](reports/figures/policy_simulation.png) — avoided vs destroyed (log) and alert volume
- [reports/replication_eval.json](reports/replication_eval.json) — rack-52 costing (5,123 rows)

## CLI entry points

After `pip install -e .`:

| Command | Purpose |
|---------|---------|
| `fpce-download` | Download Alibaba trace files |
| `fpce-select-rack` | Select homogeneous rack |
| `fpce-filter-instances` | Filter batch_instance to rack(s) |
| `fpce-build-parquet` | Build processed Parquet tables |
| `fpce-time-grid` | Build 1-min host grid (feature source) |
| `fpce-instance-events` | Build instance-level prediction table |
| `fpce-google-events` | Map Google 2019 parquet shards to attempt-level events |
| `fpce-google-quality` | Quality JSON for the Google attempt table |
| `fpce-quality-report` | Generate quality JSON |
| `fpce-freeze-split` | Freeze time-based train/test split |
| `fpce-replay` | Stream time_grid as JSONL (telemetry stub) |
| `fpce-specpower` | Scrape SPEC Power envelope (per-node, hardware-matched) |
| `fpce-supercloud` | Fit Fan form to Supercloud GPU power (shape check) |
| `fpce-cross-provider` | Train on Alibaba, score on Google (ROC-AUC / PR-AUC / lift) |
| `fpce-operator-scale` | Operator ESG PUE/WUE vs LBNL (multiplicative scale, not kWh) |
| `fpce-role-b-freeze` | Persist frozen HistGB bundle + Role B handoff parquet |
| `fpce-role-c-cost` | Fan / PUE / WUE ranges for costing-eligible handoff rows |
| `fpce-policy-sim` | Accumulate 204-row ranges; kill-at-alert vs reactive baseline |
| `fpce-policy-report` | Symmetric avoided/destroyed nets, threshold sweep, figure |
| `fpce-replication-check` | Replication-rack Fan costing (classifier if the 1.4.2 pickle loads) |
