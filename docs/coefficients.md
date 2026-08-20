# Physical cost coefficients

Operational source: [`params/physical_cost.toml`](../params/physical_cost.toml)

Role C (electrical engineer) loads this via:

```python
from fpce.costing.coefficients import load_physical_cost_params, validation_warnings

params = load_physical_cost_params()
warnings = validation_warnings()
corners = params.sweep()  # physically consistent min/max corners only
```

## Translation identities

These are the only arithmetic Role C should implement. Signatures live in `src/fpce/costing/README.md`.

| Quantity | Formula | Notes |
|----------|---------|--------|
| IT power | `P = P_idle + (P_peak − P_idle) × utilization` | Fan, Weber, Barroso (2007). `utilization` in [0, 1]. |
| IT energy | `∫ P dt` over `[decision_time, event_end)` | Convert W·s → kWh. |
| Facility energy | `IT_kWh × PUE` | Separate line item. Not an input to water. |
| Water | `IT_kWh × WUE` | Green Grid WP#35: WUE denominator is **IT energy**. |

**Cooling share of IT power is not used.** Multiplying IT kWh by 0.30–0.40 and then by WUE would double-count: WUE is already L per kWh of IT energy.

## Power model

**Reference:** Fan, Weber, Barroso (2007), ISCA '07

| Parameter | Range | Source |
|-----------|-------|--------|
| P_idle | 80–220 W | SPECpower_ssj2008 Active Idle envelope |
| P_peak | 150–450 W | SPECpower_ssj2008 100% load envelope |

SPEC curves bound a **sensitivity sweep** across comparable server classes. They are **not** mapped to individual Alibaba machines (hardware is anonymized).

`sweep()` **drops** corners where `P_idle > P_peak` (4 of 16 unfiltered corners). The idle max (220 W) exceeds the peak min (150 W) because the envelope spans different server classes; pairing those two as if they were one machine is physically impossible.

Refresh envelope from scraped curves:

```bash
fpce-specpower --limit 15 --emit-params
```

## Facility energy (PUE)

| Parameter | Range | Source |
|-----------|-------|--------|
| PUE | 1.15–1.40 | Shehabi et al. (2024), LBNL-2001637, Figure 4.6: U.S. average ~1.4 in 2023; 2028 scenario range 1.15–1.35 |

Overhead energy = IT kWh × (PUE − 1). Report this separately from water.

## Water (WUE)

| Parameter | Range | Source |
|-----------|-------|--------|
| Site WUE | 0.45–0.48 L/kWh | Shehabi et al. (2024), LBNL-2001637, Figure 4.7 |

**Definition:** WUE = annual site water (L) / IT equipment energy (kWh). The Green Grid (2011), White Paper #35 (Patterson et al.).

**Scope:** Onsite WUE only. WUE_source (offsite grid water) is out of scope.

## Sweep strategy

`PhysicalCostParams.sweep()` returns the surviving min/max combinations of `(P_idle, P_peak, PUE, WUE)` with `P_idle ≤ P_peak` (12 corners with the current envelope). Role C reports cost as a range across these corners. PUE corners affect facility energy only; water depends on idle, peak, and WUE.
