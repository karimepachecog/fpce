# Role B — Feature engineering (contract)

**Owner:** Data scientist (Role B). This directory is a contract stub; implementation belongs to Role B.

## Inputs (from Role A)

| Artifact | Path |
|----------|------|
| Instance events (target) | `data/processed/primary/instance_events.parquet` |
| Feature contract | `params/feature_contract.json` |
| Frozen time split | `data/processed/primary_time_split.json` |
| Host time grid | `data/processed/primary/time_grid.parquet` |
| Replication events (eval only) | `data/processed/replication/instance_events.parquet` |
| Google attempts (cross-provider) | `data/processed/google/attempts.parquet` |
| Google laptop sample | `data/processed/google/attempts_sample.parquet` |

## Expected outputs

| Artifact | Description |
|----------|-------------|
| Feature matrix | Admission-time instance features + host trailing window at `decision_time` |
| Trained classifier | Gradient-boosted model predicting `failed` |
| Threshold baseline | Reactive rule (e.g. retry count, runtime vs task median) for lead-time comparison |
| Lead-time events | Per true positive: `(decision_time, baseline_fire_time, event_end, waste_window_seconds)` |

## Label column

Use **`failed`** on rows with `eligible_for_training=1`. Do not train on `failure_within_horizon`.

```python
from fpce.contracts import load_feature_contract
from fpce.features.windows import join_host_at_decision

contract = load_feature_contract()
contract.assert_no_leakage(feature_frame.columns)

# Host state at admission: latest grid minute with time_stamp <= decision_time.
# Do not merge on equality only, and never use future minutes.
events = join_host_at_decision(instance_events, time_grid)
```

## Split rules

- Load `primary_time_split.json` — **do not re-split randomly**
- Train instances: `start_time < split_timestamp`
- Test instances: `start_time >= split_timestamp`
- Replication rack: evaluate once after primary-rack tuning is frozen. Costing on replication failures is allowed only after that freeze.

## Suggested module layout (Role B to implement)

```
src/fpce/features/
  windows.py      # DONE — as-of join of host grid at decision_time (Role A)
  assemble.py     # join allowed instance columns + host features
src/fpce/model/
  train.py            # fit classifier
  baseline.py         # reactive rule
  evaluate.py         # precision/recall/F1 + lead-time distribution
  cross_provider.py   # DONE — Alibaba train / Google score (Role B helper)
```
