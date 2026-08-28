# Role B — Feature engineering

**Owner:** Data scientist (Role B). Implementation is in this package; freeze summary: [docs/role_b.md](../../../docs/role_b.md).

## Inputs (from Role A)

| Artifact | Path |
|----------|------|
| Instance events (target) | `data/processed/primary/instance_events.parquet` |
| Feature contract | `params/feature_contract.json` |
| Frozen time split | `data/processed/primary_time_split.json` |
| Host time grid | `data/processed/primary/time_grid.parquet` |
| Replication events (eval only; not used for this freeze) | `data/processed/replication/instance_events.parquet` |
| Google attempts (cross-provider; not used for this freeze) | `data/processed/google/attempts.parquet` |

## Official outputs

| Artifact | Description |
|----------|-------------|
| Feature assembly | `src/fpce/features/assemble.py` (`prepare_primary_training`) |
| Host as-of join | `src/fpce/features/windows.py` (`join_host_at_decision`) |
| Frozen classifier | `models/primary_hgb_frozen.joblib` |
| Test handoff table | `reports/role_b_handoff.parquet` |

## Label column

Use **`failed`** on rows with `eligible_for_training=1`. Do not train on `failure_within_horizon`.

```python
from fpce.contracts import load_feature_contract
from fpce.features.windows import join_host_at_decision

contract = load_feature_contract()
contract.assert_no_leakage(feature_frame.columns)

# Host state at admission: latest grid minute with time_stamp <= decision_time.
events = join_host_at_decision(instance_events, time_grid)
```

## Split rules

- Load `primary_time_split.json` — **do not re-split randomly**
- Train: `start_time < split_timestamp` (`518355`)
- Test: `start_time >= split_timestamp`
- Replication rack: evaluate only after this primary freeze; costing on replication is Role C after freeze

## Modules

```
src/fpce/features/
  windows.py      # as-of join of host grid at decision_time
  assemble.py     # join allowed instance columns + host features
src/fpce/model/
  train.py            # HistGB (official)
  freeze.py           # persist bundle + C/D handoff
  baseline.py         # reactive rule
  evaluate.py         # ranking / threshold metrics
  lead_time.py        # lead time at threshold 0.9
  xgboost_train.py    # XGB v1 experiment (not official)
  xgboost_v2.py       # XGB v2 experiment (not official)
```
