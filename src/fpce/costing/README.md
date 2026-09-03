# Role C — Physical cost translation

**Owner:** Electrical engineer (Role C). Role A provides the coefficient registry; Role C implements the Fan et al. / PUE / WUE arithmetic.

## Inputs

| Source | Artifact |
|--------|----------|
| Role A | `params/physical_cost.toml` |
| Role A | `fpce.costing.coefficients.load_physical_cost_params()` |
| Role B (frozen) | `reports/role_b_handoff.parquet` — filter `eligible_for_costing=1`. Columns include `decision_time`, `event_end`, `model_alert_time`, `baseline_alert_time`. |

## Coefficient registry (Role A deliverable)

```python
from fpce.costing.coefficients import load_physical_cost_params

params = load_physical_cost_params()
corners = params.sweep()  # currently 16; drops P_idle > P_peak if the envelope ever overlaps
```

Operator-declared PUE/WUE (`fpce-operator-scale`) live in `[[operators]]`. They do **not** replace the LBNL default ranges. `operator_scale_vs_national()` reports how water and facility energy would scale for the same IT kWh.

## Translation layer (implemented)

Identities (see `docs/coefficients.md`):

1. **IT power:** `P = P_idle + (P_peak - P_idle) * utilization` (Fan, Weber, Barroso 2007)
2. **IT kWh:** integral of P over `[decision_time, event_end)`
3. **Facility kWh:** `IT_kWh * PUE` (LBNL 2024). Separate line item.
4. **Water liters:** `IT_kWh * WUE` (LBNL 2024 × Green Grid WP#35). **Do not** multiply by cooling share or PUE first.

Every output is a **range** from sweeping coefficient corners, not a point estimate.

```python
from fpce.costing.translate import translate

result = translate(utilization_series, dt_seconds, corner)
# result.it_kwh / facility_kwh / water_liters
```

CLI: `fpce-role-c-cost` writes `reports/role_c_costing.parquet` (204 primary-test rows) and `reports/role_c_costing_manifest.json`. Grid `cpu_util_percent` is divided by 100 before Fan.

Module layout:

```
src/fpce/costing/
  coefficients.py   # loader + sweep (Role A)
  translate.py      # Fan integral + PUE/WUE (Role C)
  cost_cli.py       # fpce-role-c-cost
```

`sensitivity.py` (range vs individual coefficient choices) was suggested and is not in this MVP. Replication-rack and Google costing are also out of this pass.

## Out of scope

- Cooling share of IT power — would double-count against WUE
- WUE_source (offsite grid water) — no grid-mix data available
- Per-machine SPEC mapping — trace hardware is anonymized
