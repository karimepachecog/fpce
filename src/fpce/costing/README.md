# Role C — Physical cost translation (contract)

**Owner:** Electrical engineer (Role C). Role A provides the coefficient registry; Role C implements the Fan et al. / PUE / WUE arithmetic.

## Inputs

| Source | Artifact |
|--------|----------|
| Role A | `params/physical_cost.toml` |
| Role A | `fpce.costing.coefficients.load_physical_cost_params()` |
| Role B (data scientist) | Costing-eligible true positives: `decision_time`, `event_end`, host utilization during the waste window |

## Coefficient registry (Role A deliverable)

```python
from fpce.costing.coefficients import load_physical_cost_params

params = load_physical_cost_params()
corners = params.sweep()  # drops P_idle > P_peak
```

## Translation layer (Role C to implement)

Identities (see `docs/coefficients.md`):

1. **IT power:** `P = P_idle + (P_peak - P_idle) * utilization` (Fan, Weber, Barroso 2007)
2. **IT kWh:** integral of P over `[decision_time, event_end)`
3. **Facility kWh:** `IT_kWh * PUE` (LBNL 2024). Separate line item.
4. **Water liters:** `IT_kWh * WUE` (LBNL 2024 × Green Grid WP#35). **Do not** multiply by cooling share or PUE first.

Every output must be a **range** from sweeping coefficient corners, not a point estimate.

```python
def estimate_it_kwh(utilization_series, dt_seconds, p_idle_watts, p_peak_watts) -> float:
    """Role C: implement Fan et al. integral. Not implemented in Role A."""
    raise NotImplementedError

def estimate_water_liters(it_kwh: float, wue_l_per_kwh: float) -> float:
    """Role C: it_kwh * wue. Not implemented in Role A."""
    raise NotImplementedError

def estimate_facility_kwh(it_kwh: float, pue: float) -> float:
    """Role C: it_kwh * pue. Not implemented in Role A."""
    raise NotImplementedError
```

## Suggested module layout (Role C to implement)

```
src/fpce/costing/
  coefficients.py   # DONE — loader + sweep (Role A)
  translate.py      # Role C — kWh and liter estimation
  sensitivity.py    # Role C — report range vs coefficient choices
```

## Out of scope

- Cooling share of IT power — would double-count against WUE
- WUE_source (offsite grid water) — no grid-mix data available
- Per-machine SPEC mapping — trace hardware is anonymized
