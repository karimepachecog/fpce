# Role B — Model training & evaluation (contract)

**Owner:** Data scientist (Role B). This directory is a contract stub.

See also [../features/README.md](../features/README.md) for input artifacts.

## Model choice (per proposal)

Gradient-boosted classifier on admission-time instance features plus a short host utilization window ending at `decision_time`. Output: `P(instance will fail)`.

## Evaluation metrics (required)

- Precision, recall, and F1 on primary-rack **time-based test split**, on `eligible_for_training` rows only
- Always-predict-0 and always-predict-1 as dumb baselines (positive rate is ~0.17%, so always-1 is no longer a strong baseline)
- Lead-time distribution: `baseline_fire_time - decision_time` for true positives that are `eligible_for_costing`
- Single replication pass on the failure-domain-52 rack (report regardless of outcome; same window and hardware — replication, not distribution shift)

## Outputs for Role C (electrical engineer)

For each costing-eligible true positive:

```json
{
  "instance_name": "ins_123",
  "machine_id": "m_1486",
  "decision_time": 18102,
  "baseline_fire_time": 18400,
  "event_end": 18550,
  "waste_window_seconds": 448,
  "lead_time_seconds": 298
}
```

Role C integrates host utilization over `[decision_time, event_end)` (or `[decision_time, baseline_fire_time)` for the lead-time gap) and converts to kWh / liters as a range.
