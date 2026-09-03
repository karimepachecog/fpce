# Role A handover (one page)

Clone the GitHub repo, overlay the USB onto `data/processed/`, then `pip install -e ".[dev]"`. Check SHA256 against [data/processed/HANDOFF.md](../data/processed/HANDOFF.md). Do not copy `data/raw/`, `data/interim/`, `batch_instance.parquet`, `batch_task.parquet`, `machine_usage.parquet`, or `time_grid_chunks/`.

## What to run, in order

| Step | Command | Produces |
|------|---------|----------|
| 1 | `fpce-download` | `data/raw/` Alibaba CSVs + tarball |
| 2 | `fpce-select-rack` | `rack_machine_ids.json`, `replication_rack_machine_ids.json` |
| 3 | `fpce-filter-instances` | `data/interim/batch_instance_*.csv` |
| 4 | `fpce-build-parquet` | per-rack `batch_instance` / `batch_task` / `machine_usage` parquet |
| 5 | `fpce-time-grid` | `time_grid.parquet` (host minutes; **not** the label table) |
| 6 | `fpce-instance-events` | `instance_events.parquet` (`failed`, `*_frac`, waste windows) |
| 7 | `fpce-quality-report` | `reports/data_quality.json` |
| 8 | `fpce-freeze-split --rack primary` | `primary_time_split.json` (`split_timestamp=518355`) |
| 9 | `fpce-specpower --all --emit-params` | `spec_power_curves.parquet` + SPEC envelope in TOML |
| 10 | `fpce-supercloud` | `reports/supercloud_fan_fit.json` (GPU form check only) |
| 11 | `fpce-google-events --input data/raw/google` | `attempts.parquet` + `export_manifest.json` |
| 12 | `fpce-google-events --sample-from data/processed/google/attempts.parquet` | `attempts_sample.parquet` (~1M FAIL+FINISH) |
| 13 | `fpce-google-quality` | `reports/google_quality.json` |
| 14 | `fpce-operator-scale` | `reports/operator_coefficient_scale.json` (PUE/WUE scale vs LBNL; **not** kWh) |
| 15 | `fpce-cross-provider` | `reports/cross_provider.json` (adapter smoke-test on the Google sample) |
| 16 | `fpce-role-c-cost` | `reports/role_c_costing.parquet` (204 primary-test rows; already produced) |
| 17 | `fpce-policy-sim` | `reports/policy_simulation.json` (accumulated kill vs baseline) |

B/C/D do **not** re-run 1–13 unless reproducing ingest. After clone + USB they only need `pip install -e ".[dev]"`. **Role B is frozen** — see [role_b.md](role_b.md). Official model: `models/primary_hgb_frozen.joblib` (HistGB, threshold 0.9). Handoff: `reports/role_b_handoff.parquet`. Role C costing on the 204 primary-test rows is done (`fpce-role-c-cost`). The accumulated policy result is `fpce-policy-sim` → `reports/policy_simulation.json`.

## Contracts

- Label: `failed` on `eligible_for_training=1`. Never train on `failure_within_horizon`.
- Features: `params/feature_contract.json`. Host grid join: `time_stamp <= decision_time`.
- Cross-provider: `plan_cpu_frac` / `plan_mem_frac` / `retry_index`. Never `machine_id` or raw `plan_cpu`.
- Costing: `eligible_for_costing=1` only. Grid `cpu_util_percent` is 0–100; Fan wants utilization in [0, 1].
- Default PUE/WUE: LBNL ranges in `params/physical_cost.toml`. Operator ESG points are a **scale** comparison, not a Fan run.

## After this Role B freeze (not A)

- C: primary-test costing is done (204 rows). Replication-rack **costing** is done (5,123 rows → 81–244 IT kWh, 37–117 L in `reports/replication_eval.json`). Classifier on rack 52 still needs sklearn 1.4.2. Google attempt costing stays optional.
- B+C+D: policy accounting is in `reports/policy_simulation.json`. Both kill-at-admission policies are **net-negative**; the model is ~100× less bad (net −176 to −51 IT kWh vs baseline −17,530 to −5,952). The 203/204 baseline catch rate is an artifact of the ≥60 s costing pool. `replay_summary.json` `n_costed_rows=1` is an indent bug in `runner.py`, not a finished run. Supercloud AI-compute governance stays Future Work.

Full CLI list: [README.md](../README.md). Role contracts: [roles.md](roles.md). USB file list: [HANDOFF.md](../data/processed/HANDOFF.md).
