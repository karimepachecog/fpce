# Role B — Model training & evaluation

**Owner:** Data scientist (Role B). **Frozen:** HistGradientBoosting, threshold 0.9.

Canonical write-up: [docs/role_b.md](../../../docs/role_b.md).

## Official outputs

| Artifact | Path |
|----------|------|
| Serialized model + train-only imputer/encoder | `models/primary_hgb_frozen.joblib` |
| Frozen-test scores and alerts | `reports/role_b_handoff.parquet` |
| Handoff manifest | `reports/role_b_handoff_manifest.json` |
| Classification | `reports/primary_hgb_baseline.json`, `reports/primary_hgb_thresholds.json` |
| Lead time vs reactive baseline | `reports/primary_hgb_lead_time.json` |

XGBoost reports (`reports/primary_xgb_*`, `reports/primary_xgb_v2_*`) are experiments only.

## Load the frozen scorer

```python
import joblib
from fpce.model.freeze import predict_proba_with_bundle

bundle = joblib.load("models/primary_hgb_frozen.joblib")
# features: DataFrame with bundle["feature_order"] columns
proba = predict_proba_with_bundle(bundle, features)
alert = proba >= bundle["threshold"]  # 0.9
```

## Evaluation metrics (required)

- Precision, recall, and F1 on the primary-rack **time-based test split**, on `eligible_for_training` rows only
- Always-predict-0 as a dumb baseline (prevalence ~0.1% on test)
- Lead time: `event_end - alert_time` if `alert_time < event_end`
- Reactive baseline: retry (`seq_no>=2`) or train median succeeded duration by `task_type`

## Outputs for Role C

Use `reports/role_b_handoff.parquet`. Filter `eligible_for_costing=1`. Do not compute kWh/liters in Role B. Join keys back to `instance_events.parquet`: `instance_name`, `machine_id`, `start_time`, `seq_no`, `decision_time`, `test_row_index`.
