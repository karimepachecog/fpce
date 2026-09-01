"""Physics engine for computing IT energy, facility energy, and water usage."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TranslationResult:
    it_kwh: float
    facility_kwh: float
    water_liters: float
    u_mean: float
    dt_covered_seconds: float


def translate(
    utilization_series: np.ndarray,
    dt_seconds: float,
    params: dict[str, float]
) -> TranslationResult:
    """Translate a series of host utilizations into physical cost.

    Args:
        utilization_series: 1D NumPy array of CPU utilization in [0, 1].
        dt_seconds: Total duration of the window in seconds.
        params: Physical cost corner dict with keys:
                p_idle_watts, p_peak_watts, pue, wue_l_per_kwh

    Returns:
        TranslationResult with integrated kWh and water liters.
    """
    if dt_seconds <= 0 or len(utilization_series) == 0:
        return TranslationResult(0.0, 0.0, 0.0, 0.0, 0.0)

    # 1. Clip u to [0, 1] to guard against sentinels like 101 or -1
    u_clipped = np.clip(utilization_series, 0.0, 1.0)
    
    # 2. Build time-weight array (hold strategy for last partial minute)
    # Each full minute gets up to 60s, the last minute gets the remainder to sum to dt_seconds.
    n_slots = len(u_clipped)
    weights = np.zeros(n_slots)
    
    allocated = 0.0
    for i in range(n_slots):
        remaining = dt_seconds - allocated
        if i == n_slots - 1:
            # Last slot takes everything remaining (hold strategy)
            weights[i] = max(0.0, remaining)
        else:
            weights[i] = min(60.0, max(0.0, remaining))
        allocated += weights[i]

    total_weight = np.sum(weights)
    u_mean = float(np.average(u_clipped, weights=weights)) if total_weight > 0 else 0.0
    
    # 3. Compute P(t) = P_idle + (P_peak - P_idle) * u per slot
    p_idle = params["p_idle_watts"]
    p_peak = params["p_peak_watts"]
    p_watts = p_idle + (p_peak - p_idle) * u_clipped
    
    # 4. Integrate W*s -> divide by 3.6e6 -> kWh
    energy_ws = np.sum(p_watts * weights)
    it_kwh = energy_ws / 3.6e6
    
    # 5. E_facility = E_IT * PUE; V_water = E_IT * WUE
    pue = params["pue"]
    wue = params["wue_l_per_kwh"]
    
    facility_kwh = it_kwh * pue
    water_liters = it_kwh * wue
    
    return TranslationResult(
        it_kwh=float(it_kwh),
        facility_kwh=float(facility_kwh),
        water_liters=float(water_liters),
        u_mean=u_mean,
        dt_covered_seconds=total_weight,
    )
