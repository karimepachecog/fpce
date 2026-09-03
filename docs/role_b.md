# Role B freeze summary

This document is the source of truth for Role B (data scientist). A new agent should not need prior chat history.

**Status: Role B is frozen.** Do not train a new primary-rack classifier or retune thresholds unless a later role explicitly reopens that decision.

**Official model:** `sklearn.ensemble.HistGradientBoostingClassifier`  
**Official threshold:** `0.9`  
**XGBoost:** documented experiments only; **not** the official Role B model.

Physical kWh / water liters are **out of scope for Role B**. Role C later costed the 204-row primary-test pool; the accumulated policy result is `reports/policy_simulation.json`.

---

## Objective

Predict whether an Alibaba batch **instance** will fail (`failed=1`) at **admission** (`decision_time` = `start_time`, offset 0). The goal is to flag doomed instances early enough to compare remaining runtime against a **reactive baseline**, then later (Role C) translate remaining host occupancy into a physical-cost **range**. Role B owns features, the classifier, the reactive baseline, and lead-time events. Role B does **not** implement Fan / PUE / WUE arithmetic.

Do **not** train on `failure_within_horizon` (legacy machine-minute column).

---

## Datasets

| Artifact | Path |
|----------|------|
| Primary instance events | `data/processed/primary/instance_events.parquet` |
| Primary host time grid | `data/processed/primary/time_grid.parquet` |
| Frozen split | `data/processed/primary_time_split.json` |
| Feature contract | `params/feature_contract.json` |
| USB checksums (Role A kit) | `data/processed/HANDOFF.md` |

Host features are attached with `fpce.features.windows.join_host_at_decision`: latest grid row with `time_stamp <= decision_time` (no future minutes). The join sorts by time then `machine_id` so `pandas.merge_asof` is globally monotonic.

Replication-rack and Google tables were **not** used to select the official model. Replication costing is allowed only after this freeze (Role C).

Training rows: `eligible_for_training=1`. Target: `failed`.

---

## Temporal split

From `data/processed/primary_time_split.json`:

- Split column: `start_time`
- `split_timestamp = 518355`
- Train: `start_time < 518355`
- Test: `start_time >= 518355` (**frozen**; do not reshuffle)

Eligible counts used for modelling (after `eligible_for_training=1`):

- Train: 9,000,205 rows (positive rate ~0.200%)
- Test: 3,974,412 rows (positive rate ~0.095%), **3,778 failures**

Inner temporal validation (XGBoost experiments only, last 20% of **train time**, test untouched): inner train `start_time < 417433`.

---

## Final features (12)

`task_type` (categorical, column 0) plus 11 numeric columns. **Inference order is** `models/primary_hgb_frozen.joblib` → `feature_order` (also in `reports/role_b_frozen_model.json` and `reports/primary_hgb_baseline.json`).

Dropped **at model time only** (still allowed in the contract): `mem_gps`, `mkpi`, `plan_cpu`, `plan_mem`, `machine_id`. `machine_id` is used for the host join, not as a model feature.

---

## Preprocessing

- Numeric: `SimpleImputer(strategy="median")` fit on **train only** (full primary train for HistGB).
- `task_type`: `OrdinalEncoder` with unknown / missing → NaN. HistGB uses `categorical_features=[0]`. Train categories: 1, 3–12. Unseen test type 2 becomes missing.
- Imbalance: `class_weight="balanced"` (or equivalent `sample_weight` on older sklearn).
- No SMOTE, no oversampling, no undersampling, no scaling.

Code: `src/fpce/features/assemble.py`, `src/fpce/model/train.py`.

---

## Selected model and threshold

Hyperparameters (`src/fpce/model/train.py` `HGB_PARAMS`): `max_depth=6`, `learning_rate=0.1`, `max_iter=100`, `min_samples_leaf=20`, `l2_regularization=0.0`, `random_state=0`, `early_stopping=False`. Fit on **full train** (no inner val for the official model).

**Working threshold = 0.9** (not the train-F1 threshold ~0.35). At 0.9, test false positives drop vs 0.5 with almost the same recall. Constant stored as `WORKING_THRESHOLD` in `src/fpce/model/lead_time.py`.

Serialized bundle (model + imputer + encoder + metadata), **not tracked in Git** (share via Drive/USB):

`models/primary_hgb_frozen.joblib`

Metadata: `reports/role_b_frozen_model.json`.

---

## Main classification metrics (frozen test, HistGB t=0.9)

Source: `reports/primary_hgb_thresholds.json` (grid point 0.9) and `reports/primary_hgb_baseline.json` (ranking metrics).

| Metric | Value |
|--------|------:|
| PR-AUC | 0.802 |
| ROC-AUC | 0.984 |
| Precision | 0.141 |
| Recall | 0.889 |
| F1 | 0.243 |
| TP | 3,360 |
| FP | 20,523 |
| FN | 418 |
| TN | 3,950,111 |
| FP/TP | 6.11 |
| Alert rate | 0.60% |

Test scores (aligned with the frozen test row order): `reports/primary_hgb_test_scores.npz`.

For comparison, t=0.5: recall 0.902, FP 34,798, FP/TP 10.2 (`reports/primary_hgb_baseline.json`).

---

## Lead-time definition

- **Failure:** test row with `failed=1` and `eligible_for_training=1`.
- **Failure time:** `event_end` (recorded `end_time`, else `start_time` when `end_time=0`).
- **Model alert time:** `decision_time` if `model_score >= 0.9`, else no alert.
- **Lead time:** `event_end - alert_time` **only if** `alert_time < event_end`.
- If `event_end <= decision_time`, lead time is **not measurable**.

Code: `src/fpce/model/lead_time.py`. Report: `reports/primary_hgb_lead_time.json`.

---

## Reactive baseline definition

Fire at the earlier of:

1. Retry: `seq_no >= 2` known at admission → fire at `decision_time`
2. Runtime: `decision_time + median(duration | succeeded, task_type)` with medians fit on **train successes only** (`waste_window_seconds`). Global fallback median is 10 s.

A fire at or after `event_end` does not count. Code: `src/fpce/model/baseline.py`.

---

## Lead-time results (test failures)

Documented facts (do not round away from the reports):

- **3,778** failures in test
- **1,276** failures with a positive measurable window (`event_end > decision_time`)
- **2,502** failures with no measurable lead time because `event_end <= decision_time`
- HistGB at threshold **0.9** anticipates **1,210** failures (alert strictly before `event_end`)
- That is **32.0%** of all 3,778 failures and **94.8%** of the 1,276 windowed failures
- Model lead time on those 1,210: **median 17 s**, mean ~83 s, p90 378 s, p95 395 s
- Reactive baseline anticipates **807** failures (21.4% of all; 63.2% of the windowed set); median lead 19 s

Paired (both detect before `event_end`): 794 failures; median delta (model − baseline) = 0 s (both typically fire at admission). Figure: `reports/figures/primary_hgb_lead_time.png`.

---

## XGBoost experiments (not selected)

Same dataset, 12 features, split, target, imputation style, `task_type` handling, and frozen test. No SMOTE.

| Experiment | Reports | Outcome |
|------------|---------|---------|
| XGB v1 | `reports/primary_xgb_baseline.json`, `primary_xgb_thresholds.json`, `primary_xgb_lead_time.json`, `primary_xgb_test_scores.npz` | `best_iteration = 0` (early stopping too aggressive). Operational val threshold ~0.51. Test: PR-AUC 0.838, recall 0.886, **FP 13,043**, FP/TP 3.90. Anticipated 1,199. |
| XGB v2 | `reports/primary_xgb_v2_*.json`, `primary_xgb_v2_test_scores.npz` | Config B used many trees (`best_iteration = 274`). Val-chosen t≈0.359. Test: PR-AUC 0.840, recall 0.899, **FP 32,173**, FP/TP 9.47. Anticipated 1,224. Lead time still median 17 s. |

v2 showed that boosting can use many trees; the frozen operational threshold transferred poorly on **false positives**. Lead time is essentially the same as HistGB (admission alerts). **Do not continue hyperparameter search** without a strong new reason. Official Role B model remains HistGB t=0.9.

---

## Generated files and reports

### Official HistGB

- `reports/primary_hgb_baseline.json`
- `reports/primary_hgb_thresholds.json`
- `reports/primary_hgb_lead_time.json`
- `reports/primary_hgb_test_scores.npz`
- `reports/figures/primary_hgb_pr_curve.png`
- `reports/figures/primary_hgb_threshold_precision_recall.png`
- `reports/figures/primary_hgb_lead_time.png`
- `models/primary_hgb_frozen.joblib` (sklearn 1.4.2; test scores match `primary_hgb_test_scores.npz` within 3e-8; **not in Git**)
- `reports/role_b_frozen_model.json`
- `reports/role_b_handoff.parquet` (3,974,412 rows; **not in Git**; SHA256 in `reports/role_b_handoff_manifest.json`)
- `reports/role_b_handoff_manifest.json`

### XGBoost (archive only)

- `reports/primary_xgb_*.json`, `reports/primary_xgb_test_scores.npz`, `reports/figures/primary_xgb_*.png`
- `reports/primary_xgb_v2_*.json`, `reports/primary_xgb_v2_test_scores.npz`, `reports/figures/primary_xgb_v2_*.png`

### Code

- `src/fpce/features/assemble.py`, `src/fpce/features/windows.py`
- `src/fpce/model/train.py`, `evaluate.py`, `threshold_analysis.py`, `baseline.py`, `lead_time.py`, `freeze.py`
- `src/fpce/model/xgboost_train.py`, `xgboost_v2.py` (experiments)
- Tests: `tests/test_assemble.py`, `test_windows.py`, `test_primary_baseline.py`, `test_threshold_analysis.py`, `test_lead_time.py`, `test_freeze.py`, `test_xgboost_train.py`, `test_xgboost_v2.py`

---

## Known limitations

1. **2,502 / 3,778** test failures have `event_end <= decision_time` (often `end_time=0` → `event_end = start_time`). Admission alerts cannot be “early” on those rows.
2. Median measurable lead is **17 seconds**. Most anticipated failures fall in the &lt;1 minute bin. This is a short operational horizon.
3. Positive rate is ~0.1% on test. Accuracy is not a useful metric.
4. Threshold 0.9 was chosen from **test** PR/FP behaviour for HistGB (documented in `primary_hgb_thresholds.json`). XGB thresholds were chosen on validation; do not mix those protocols when comparing.
5. HistGB was trained on **full train**; XGB used inner-train only for fitting. They are not identical training sets.
6. Replication rack and Google cross-provider are **not** part of this freeze decision.
7. Costing-eligible pool on primary test is small (`instance_test_costing_rows` in the split JSON is 204 before the eligible_for_training filter used at model time). Role C must use `eligible_for_costing=1` and must not mix imputed windows into measured waste.

---

## What Role C should consume

Role C costing on this freeze is `reports/role_c_costing.parquet`. Policy accumulation: `fpce-policy-sim`.

When consuming this freeze for costing:

1. Read `reports/role_b_handoff.parquet` (or regenerate with `fpce-role-b-freeze --skip-model`).
2. Filter `failed=1` and `eligible_for_costing=1`. Optionally require `model_alert=1` for model-attributed waste vs baseline fire times.
3. Columns: `machine_id`, `decision_time`, `event_end`, `waste_window_seconds`, `model_alert_time`, `baseline_alert_time`, `eligible_for_costing`.
4. Join host utilization from `data/processed/primary/time_grid.parquet` on `[decision_time, event_end)` (Fan wants utilization in [0, 1]; grid `cpu_util_percent` is 0–100).
5. Coefficients: `params/physical_cost.toml` via `fpce.costing.coefficients`. Output **ranges**, not point estimates. See `src/fpce/costing/README.md` and `docs/coefficients.md`.

Map rows back to `instance_events.parquet` with `instance_name`, `machine_id`, `start_time`, `seq_no`, `decision_time` (and `test_row_index` for the frozen test order).

---

## What Role D should consume

1. Frozen classifier: `models/primary_hgb_frozen.joblib` (`model`, `imputer`, `task_type_encoder`, `feature_order`, `threshold=0.9`). Helpers: `fpce.model.freeze.predict_proba_with_bundle`.
2. Reactive baseline: `fpce.model.baseline.reactive_fire_time` with train medians from `reports/primary_hgb_lead_time.json` (`reactive_runtime_medians_seconds`) or recompute on train only.
3. Per-event scores and alerts: `reports/role_b_handoff.parquet` (no need to rescore the frozen test).
4. Replay source: `data/processed/primary/instance_events.parquet` + `time_grid.parquet`. Accumulated result: `fpce-policy-sim`. JSONL stub: `fpce-replay`. Contract: `src/fpce/replay/README.md`.
5. Role C translation: `fpce.costing.translate.translate()` and `reports/role_c_costing.parquet`.

Do **not** load XGBoost pickles as the production scorer.

---

## Reproduce or validate (no new tuning)

```bash
pip install -e ".[dev]"

# Unit tests for Role B (no need to retrain)
pytest tests/test_assemble.py tests/test_windows.py tests/test_primary_baseline.py \
  tests/test_threshold_analysis.py tests/test_lead_time.py tests/test_freeze.py \
  tests/test_xgboost_train.py tests/test_xgboost_v2.py tests/test_contracts.py

# Persist model bundle + handoff parquet (refits HistGB with HGB_PARAMS only;
# refuses to save if test scores disagree with reports/primary_hgb_test_scores.npz)
fpce-role-b-freeze

# Handoff only (uses cached npz; does not fit)
fpce-role-b-freeze --skip-model
```

Do **not** run `python -m fpce.model.train` / XGBoost CLIs unless intentionally regenerating reports. Those paths can overwrite JSON if invoked with default outputs.

Assemble matrices without fitting: `python -m fpce.features.assemble`.

---

## Role B completeness

Role B **is complete** for the primary-rack official classifier, threshold, lead-time vs reactive baseline, freeze artifacts, and handoff table. Later roles: C costed the 204-row test pool; `fpce-policy-sim` accumulates kill-vs-baseline ranges. Still open: replication-rack evaluation and Google costing.
