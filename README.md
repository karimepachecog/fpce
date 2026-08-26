# FPCE Datacenter Project

Fault-triggered Physical Cost Estimator (FPCE) — predicting doomed batch instances on the Alibaba cluster trace and translating their remaining runtime into estimated physical cost (kWh and liters of cooling water).

## Role map

| Role | Discipline | Owns | Key outputs |
|------|------------|------|-------------|
| **A — Data engineer** | Pipeline & data | Trace ingestion, instance labels, coefficient registry | `instance_events.parquet`, `time_grid.parquet`, `params/physical_cost.toml` |
| **B — Data scientist** | ML | Features, classifier, reactive baseline, lead-time events | Model artifacts (`src/fpce/features/`, `src/fpce/model/`) |
| **C — Electrical engineer** | Physical cost | Fan et al. power model + PUE + WUE | kWh/liter **ranges** per doomed instance (`src/fpce/costing/`) |
| **D — Software engineer** | Integration | Replay harness, experiment runner | End-to-end result (`src/fpce/replay/`) |

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
  features/                   # Role B contract (stub)
  model/                      # Role B contract (stub)
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

## What Role B (data scientist) consumes

- **Training:** `data/processed/primary/instance_events.parquet` + `primary_time_split.json`
- **Host context:** `data/processed/primary/time_grid.parquet` via `fpce.features.windows.join_host_at_decision` (`time_stamp <= decision_time`)
- **Contract:** `params/feature_contract.json` — call `fpce.contracts.assert_no_leakage`
- **Replication eval:** `data/processed/replication/instance_events.parquet` (same window/hardware; replication, not distribution shift)
- **Cross-provider eval:** `data/processed/google/attempts_sample.parquet` on a laptop; full `attempts.parquet` on 16+ GB RAM. `fpce-cross-provider` defaults to the sample if present. Use `plan_cpu_frac` / `plan_mem_frac`, never raw `plan_cpu` or `machine_id`.
- **Column dictionary:** [docs/data_dictionary.md](docs/data_dictionary.md)

The machine-minute column `failure_within_horizon` is **not** the target (~37% positive). `failed` on instance events is.

## What Role C (electrical engineer) consumes

- Costing-eligible true positives from Role B (`eligible_for_costing=1`)
- Host utilization during `[decision_time, event_end)`
- **`params/physical_cost.toml`** via `fpce.costing.coefficients.load_physical_cost_params()`
- Water = IT kWh × WUE. Facility energy = IT kWh × PUE. No cooling share.

## What Role D (software engineer) consumes

- Trained classifier and reactive baseline from Role B
- Cost translation API from Role C
- Instance events + time grid for simulated real-time replay (`fpce-replay`)

## Documentation

- [docs/handover.md](docs/handover.md) — one-page Role A handover (script order, USB, what B/C/D do next)
- [docs/pipeline.md](docs/pipeline.md) — stage-by-stage ingestion
- [docs/roles.md](docs/roles.md) — handoff contracts
- [docs/data_dictionary.md](docs/data_dictionary.md) — instance events + time grid
- [docs/coefficients.md](docs/coefficients.md) — physical cost identities and provenance
- [docs/google_export.md](docs/google_export.md) — BigQuery export schema and Role A/B handoff for Google 2019
- [reports/data_quality.md](reports/data_quality.md) — measured dataset summary

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
| `fpce-replay` | Stream time_grid as JSONL |
| `fpce-specpower` | Scrape SPEC Power envelope (per-node, hardware-matched) |
| `fpce-supercloud` | Fit Fan form to Supercloud GPU power (shape check) |
| `fpce-cross-provider` | Train on Alibaba, score on Google (ROC-AUC / PR-AUC / lift) |
| `fpce-operator-scale` | Operator ESG PUE/WUE vs LBNL (multiplicative scale, not kWh) |
