# Fault-triggered Physical Cost Estimator (FPCE)

FPCE scores batch jobs at admission for the likelihood that they will fail, then converts remaining runtime into ranges of IT energy (kWh), facility energy (kWh), and onsite cooling water (L). The estimates come from public production traces and published engineering coefficients. Interrupting a flagged job is treated as an operating decision: wasted work avoided is counted, and so is useful work discarded when a successful job is stopped by error.

The manuscript is [What_a_Failure_Costs.md](What_a_Failure_Costs.md). A one-page briefing is [docs/exec_summary.md](docs/exec_summary.md).

## How the pipeline works

```
raw traces  →  instance table + host time grid
            →  admission features (no post-failure leakage)
            →  failure probability
            →  Fan / PUE / WUE energy and water ranges
            →  policy accounting (interrupt vs leave running)
```

1. **Ingestion.** The Alibaba cluster trace is reduced to one homogeneous rack of 40 machines (failure domain 51) and a second rack of the same class (domain 52) reserved for replication. Each batch instance becomes one prediction row (`instance_events.parquet`) with a binary `failed` label. Host telemetry is aligned on a one-minute grid (`time_grid.parquet`) used only as a feature source.

2. **Split.** Training and test are cut by instance start time (`primary_time_split.json`), not by random row. The selected classifier is never trained on the replication rack.

3. **Features.** Allowed columns are listed in `params/feature_contract.json`. The training target is `failed` on rows marked eligible for training. `failure_within_horizon` is not a training target.

4. **Classifier.** HistGradientBoosting, decision threshold **0.9**, persisted as `models/primary_hgb_frozen.joblib`. XGBoost artifacts under `reports/` are experiments only.

5. **Physical cost.** Remaining runtime is converted with the Fan et al. linear power model at 16 coefficient corners (`params/physical_cost.toml`): idle and peak watts, PUE, and WUE. Facility energy is IT kWh × PUE. Water is IT kWh × WUE. Costing is applied only to held-out failures with measured duration of at least 60 seconds.

6. **Policy.** Two interrupt rules are accumulated on that costing pool and on false positives: the classifier at threshold 0.9, and a reactive runtime baseline. Net energy is avoided waste minus healthy work destroyed. The same 204-row pool can be costed independently with `python -m fpce.replay.runner`. Headline avoided-versus-destroyed accounting is `fpce-policy-sim`.

A parallel path maps Google Borg 2019 event shards to **attempts** (schedule to next terminal event) and scores them with a model trained on Alibaba. Requested CPU and memory must be compared as fractions of the largest machine (`plan_cpu_frac` / `plan_mem_frac`); raw `plan_cpu` units are not interchangeable.

Stage-by-stage inputs and outputs: [docs/pipeline.md](docs/pipeline.md).

## Headline numbers (held-out Alibaba test)

Failures occur in about one job per thousand. On the time-split test set the selected classifier has PR-AUC **0.802** and ROC-AUC **0.984**. At threshold 0.9, precision is **0.141** and recall **0.889**.

Costing is restricted to **204** test failures with duration ≥ 60 s. If they run to completion they account for **3.39–9.97 IT kWh** (1.52–4.79 L). Interrupting at threshold 0.9 avoids most of that quantity and also discards **60–179 IT kWh** of successful work (net **−176 to −51** IT kWh). A reactive rule that alerts on 46.5% of test rows is about 100× more destructive. Energy-neutrality against the costed failures requires about **15%** precision; observed precision on that pool is about **1%**.

On the unused replication rack (13.0 million scored rows) ranking quality is comparable (ROC-AUC **0.986**, PR-AUC **0.861**). Prevalence is higher (0.170% vs 0.095%), so precision is not directly comparable.

Full tables, lead time, Google transfer, and limitations are in the manuscript.

## Setup

Python 3.11 or 3.12. To load the persisted HistGradientBoosting bundle, install **scikit-learn 1.4.2** and **NumPy 1.26** (not 2.x).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install "scikit-learn==1.4.2" "numpy<2"

python scripts/validate_config.py
pytest
```

XGBoost tests require OpenMP (`libomp` on macOS). They are skipped if it is not present. The selected classifier does not use XGBoost.

## Running the pipeline

Full Alibaba ingestion needs on the order of 25 GB for `batch_instance` and is started with:

```bash
bash scripts/run_ingest.sh
```

That script runs download, rack selection, instance filter, parquet build, time grid, instance events, quality report, and the time split. Coefficient envelopes:

```bash
fpce-specpower
fpce-supercloud   # GPU form check only; does not set the TOML corners
```

After processed tables exist:

```bash
fpce-role-b-freeze          # persist classifier + scored test table
fpce-role-c-cost            # Fan / PUE / WUE ranges on costing-eligible rows
fpce-policy-sim
fpce-policy-report
fpce-replication-check      # unused rack, same hardware class
python -m fpce.replay.runner
```

`fpce-replay` streams the time grid as JSONL (telemetry stub). It is not the costing harness.

Google event shards (`data/raw/google/*.parquet`) are collapsed with `fpce-google-events`, checked with `fpce-google-quality`, and scored with `fpce-cross-provider`.

## Data

Git tracks code, documentation, small JSON (splits, rack ids, quality reports), and the SPEC Power envelope. Prediction tables are large and are not in git.

**Required for scoring, costing, and policy (under `data/processed/`):**

| Path | Use |
|------|----------------------|
| `primary/instance_events.parquet` | Prediction unit and labels (training rack) |
| `primary/time_grid.parquet` | Host features |
| `primary_time_split.json` | Time-based train/test cut |
| `replication/instance_events.parquet` | Unused rack, same machine class |
| `replication/time_grid.parquet` | Host features for that rack |
| `rack_machine_ids.json` | Primary rack membership |
| `replication_rack_machine_ids.json` | Replication rack membership |
| `spec_power_curves.parquet` | Hardware-matched power envelope |
| `google/attempts.parquet` | Borg attempts (full cell) |
| `google/attempts_sample.parquet` | Stratified laptop sample for cross-provider eval |
| `google/export_manifest.json` | Export provenance |

Checksums: [data/processed/HANDOFF.md](data/processed/HANDOFF.md). Do not copy `data/raw/` or `data/interim/` unless you are re-running ingestion. Intermediate Alibaba tables (`batch_instance.parquet`, `batch_task.parquet`, `machine_usage.parquet`, `time_grid_chunks/`) are ingestion outputs, not classifier inputs.

### Google 2019 event export

The export in `data/raw/google/` is **many parquet shards**, not one file. BigQuery writes a large result as `extraccion_000000000000.parquet`, …. Each file is a slice of rows; events for the same instance are scattered and unsorted.

Each row is a **lifecycle event** (submitted, scheduled, failed, finished, evicted, killed), not a task. After eviction, Borg often schedules the instance again; that second schedule is a new attempt, analogous to a new Alibaba `batch_instance` with a higher `seq_no`. `fpce-google-events` groups events into attempts with DuckDB (the table does not fit in RAM).

| Column in the export | Meaning |
|----------------------|---------|
| `collection_id`, `instance_index` | Which Borg instance |
| `time` | Microseconds from 600 s before trace start |
| `type` | Event code: 0 submit, 3 schedule, 4 evict, 5 fail, 6 finish, 7 kill |
| `cpus_request`, `memory_request` | Requested resources, as a **fraction of the largest machine in the cell** |
| `machine_id`, `priority`, `scheduling_class` | Optional context |

Alibaba `plan_cpu` is hundredths of a core on a 96-thread machine; divide by 9,600 to obtain the same unit as Google. `machine_id` is not comparable across providers. See [docs/google_export.md](docs/google_export.md).

## Repository layout

```
params/physical_cost.toml     # idle/peak watts, PUE, WUE (cited ranges)
params/feature_contract.json  # columns allowed at admission
src/fpce/
  ingest/                     # traces → instance events and time grid
  features/                   # join and windows
  model/                      # classifier, split, cross-provider, replication
  costing/                    # Fan / PUE / WUE
  replay/                     # policy accumulation and costing runner
  provenance/                 # SPEC Power envelope
models/                       # persisted HistGradientBoosting bundle
reports/                      # metrics, policy JSON, figures
data/processed/               # tables above (gitignored parquets)
docs/
```

## Commands

After `pip install -e .`:

| Command | Stage |
|---------|--------|
| `fpce-download` | Fetch Alibaba trace files |
| `fpce-select-rack` | Choose homogeneous 40-machine racks |
| `fpce-filter-instances` | Restrict `batch_instance` to those racks |
| `fpce-build-parquet` | Write processed Alibaba tables |
| `fpce-time-grid` | Build 1-minute host grid |
| `fpce-instance-events` | Build instance-level prediction table |
| `fpce-quality-report` | Dataset summary JSON |
| `fpce-freeze-split` | Write the time-based train/test split |
| `fpce-specpower` | SPEC Power envelope (per-node, hardware-matched) |
| `fpce-supercloud` | Fan-form check on Supercloud GPU power |
| `fpce-google-events` | Map Google shards to attempts |
| `fpce-google-quality` | Quality JSON for the attempt table |
| `fpce-cross-provider` | Train on Alibaba, score Google attempts |
| `fpce-role-b-freeze` | Persist classifier and scored test table |
| `fpce-role-c-cost` | Energy/water ranges for costing-eligible rows |
| `fpce-policy-sim` | Interrupt vs reactive baseline on the costing pool |
| `fpce-policy-report` | Avoided/destroyed nets, threshold sweep, figure |
| `fpce-replication-check` | Score and cost the unused rack |
| `fpce-operator-scale` | Operator ESG PUE/WUE vs LBNL (ratio scale, not kWh) |
| `fpce-replay` | Stream `time_grid` as JSONL |

## Documentation

- [What_a_Failure_Costs.md](What_a_Failure_Costs.md) — manuscript
- [docs/exec_summary.md](docs/exec_summary.md) — executive briefing
- [docs/exec_slides.md](docs/exec_slides.md) — slide outline
- [docs/pipeline.md](docs/pipeline.md) — ingestion and evaluation stages
- [docs/data_dictionary.md](docs/data_dictionary.md) — instance events and time grid
- [docs/coefficients.md](docs/coefficients.md) — physical-cost identities and provenance
- [docs/google_export.md](docs/google_export.md) — Borg event schema and attempt mapping
- [reports/data_quality.md](reports/data_quality.md) — measured dataset summary
- [reports/policy_simulation.json](reports/policy_simulation.json) — avoided, destroyed, and net kWh/L
- [reports/policy_threshold_sweep.json](reports/policy_threshold_sweep.json) — net vs threshold; break-even precision
- [reports/figures/policy_simulation.png](reports/figures/policy_simulation.png) — avoided vs destroyed and alert volume
- [reports/replication_eval.json](reports/replication_eval.json) — unused-rack classifier and costing
