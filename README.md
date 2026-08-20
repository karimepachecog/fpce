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

## What Role B (data scientist) consumes

- **Training:** `data/processed/primary/instance_events.parquet` + `primary_time_split.json`
- **Host context:** `data/processed/primary/time_grid.parquet` joined at `time_stamp <= decision_time`
- **Contract:** `params/feature_contract.json` — call `fpce.contracts.assert_no_leakage`
- **Replication eval:** `data/processed/replication/instance_events.parquet` (same window/hardware; replication, not distribution shift)
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

- [docs/pipeline.md](docs/pipeline.md) — stage-by-stage ingestion
- [docs/roles.md](docs/roles.md) — handoff contracts
- [docs/data_dictionary.md](docs/data_dictionary.md) — instance events + time grid
- [docs/coefficients.md](docs/coefficients.md) — physical cost identities and provenance
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
| `fpce-quality-report` | Generate quality JSON |
| `fpce-freeze-split` | Freeze time-based train/test split |
| `fpce-replay` | Stream time_grid as JSONL |
| `fpce-specpower` | Scrape SPEC Power envelope |
