# Role D — Simulation & interface (contract)

**Owner:** Software engineer (Role D). This directory is a contract stub; Role D implements the full experiment runner.

## Purpose

Proposal Methodology §4: a replay harness executes the trained classifier against held-out instances in simulated real time (an instance is scored at `decision_time`), logs predicted-versus-baseline lead times, and reports the accumulated physical-cost range across costing-eligible true positives.

## Inputs

| Source | Deliverable |
|--------|-------------|
| Role A | `instance_events.parquet` + `time_grid.parquet` |
| Role B | Frozen HistGB: `models/primary_hgb_frozen.joblib` (threshold 0.9). Per-event table: `reports/role_b_handoff.parquet`. Reactive rule: `fpce.model.baseline`. Summary: `docs/role_b.md` |
| Role C | `translate()` API returning kWh/liter ranges per doomed instance (**not implemented yet**) |

## Current stub

`fpce.replay.stream` replays the time grid as JSONL at configurable speed. Role D extends this to:

1. Score each instance at `decision_time` with Role B's classifier
2. Detect reactive-baseline firing on the same instances
3. Call Role C's translator for costing-eligible true positives
4. Aggregate and report lead-time distribution + cost range

## Suggested module layout (Role D to implement)

```
replay/
  stream.py       # DONE — JSONL telemetry replay (Role A grid → stream)
  runner.py       # Role D — orchestrate classifier + baseline + costing
  report.py       # Role D — aggregate lead times and cost ranges
```

## CLI

```bash
fpce-replay --rack primary --output replay.jsonl --limit 100
```

Role D may add `fpce-run-experiment` once B and C deliver their APIs.
