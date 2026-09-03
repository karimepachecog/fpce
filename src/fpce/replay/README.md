# Role D — Simulation and policy

**Owner:** Software engineer (Role D).

Proposal Methodology §4: execute the frozen classifier against held-out instances, log predicted-versus-baseline lead times, and report the accumulated physical-cost range across costing-eligible true failures.

## Headline

Both admission-kill policies are **net-negative** on the 204-row costing pool. The HistGB at t=0.9 is **~100× less bad** than the reactive baseline because it fires on 0.6% of the frozen test (23,883 alerts), not 46.5% (1,849,651).

The 203/204 “baseline caught them” figure is a **selection artifact**: costing rows last ≥ 60 s and the baseline fires at ~10 s.

## What to run

```bash
fpce-policy-sim
fpce-policy-report
```

Writes:

- `reports/policy_simulation.json` — avoided, destroyed, and net ranges
- `reports/policy_simulation.parquet` — one row per costing-eligible failure (gitignored)
- `reports/policy_threshold_sweep.json` — net vs threshold; break-even precision
- `reports/figures/policy_simulation.png` — avoided vs destroyed (log) and alert volume

Destroyed useful work is charged to **both** policies. Baseline FPs (1.85M) are Fan-costed on a 50,000-row sample (`seed=0`) and scaled.

At the official t=0.9, break-even vs costing TPs needs ~15% precision; observed is ~1%. A post-hoc sweep is net-positive at t=0.999 (196 TPs, 3 FPs). That cut is **not** the frozen Role B threshold.

## Known runner bug

`src/fpce/replay/runner.py` converts `cpu_util_percent` **outside** the per-row loop, so only the last eligible row is costed. `reports/replay_summary.json` `n_costed_rows=1` is that bug. Do not cite it as the experiment.

## Module layout

```
replay/
  stream.py   # fpce-replay — JSONL telemetry replay
  policy.py   # fpce-policy-sim — 204-row Fan tails
  report.py   # fpce-policy-report — symmetric accounting + figure
  runner.py   # broken full-rescoring path (indent bug)
```
