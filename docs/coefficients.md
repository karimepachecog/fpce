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
| P_idle | 40.1–176.0 W | SPECpower_ssj2008 Active Idle, **per node**, 19 two-socket systems with 88–96 hardware threads |
| P_peak | 241.0–650.0 W | SPECpower_ssj2008 100% load, same matched subset |

SPEC results with `# of Identical Nodes > 1` report **aggregate** watts. `fpce.provenance.specpower` divides by that count before computing the envelope. The matched filter is 2 sockets and ~96 hardware threads (Alibaba rack `cpu_num=96`, ±10%). The unfiltered 1,116-result envelope is far wider (idle 9–1,308 W) and is **not** what Role C should sweep: it mixes blades with four-socket machines.

The previous registry values (idle 80–220 W, peak 150–450 W) did **not** come from `summarize_power_envelope()` despite citing it. `tests/test_provenance.py` now locks the TOML ranges to the parquet.

Because the matched idle max (176 W) is below the matched peak min (241 W), `sweep()` currently keeps all 16 corners.

Refresh:

```bash
fpce-specpower --all --emit-params
```

## Supercloud (form check only)

`fpce-supercloud` downloads MIT Supercloud HPCA'22 `dcgm.csv` (~14 MB) and fits `P = a + b × u` to GPU (SM utilization, watts). On the current file this yields **R² = 0.79** (n = 95,182). That supports using the linear Fan et al. form; it does **not** supply Alibaba CPU coefficients. Do not copy those idle/peak numbers into this TOML.

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

## Operator-declared PUE / WUE (not a Fan run)

`[[operators]]` in the TOML holds cited fleet/portfolio averages (Google 2023 PUE 1.10; Microsoft FY24 PUE 1.16 / WUE 0.30 L/kWh; Meta 2023 PUE 1.08 / WUE 0.18 L/kWh; Equinix 2024 PUE 1.39 / WUE 0.95 L/kWh all-sites; AWS 2024 PUE 1.15 / WUE 0.15 L/kWh). Default costing still uses the LBNL ranges.

`fpce-operator-scale` writes `reports/operator_coefficient_scale.json`. For a fixed IT kWh, facility energy scales with PUE and water with WUE (`vs_min` / `vs_max` = operator value ÷ LBNL range endpoint). This is **not** validation against measured facility water and **not** Role C's kWh/liter range.

## Sweep strategy

`PhysicalCostParams.sweep()` returns the surviving min/max combinations of `(P_idle, P_peak, PUE, WUE)` with `P_idle ≤ P_peak`. With the matched envelope all 16 corners survive. Role C reports cost as a range across these corners. PUE corners affect facility energy only; water depends on idle, peak, and WUE.
