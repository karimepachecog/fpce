"""Load and validate the physical cost coefficient registry."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

from fpce.config import PHYSICAL_COST_TOML


@dataclass(frozen=True)
class NumericRange:
    min: float
    max: float
    unit: str
    reference: str = ""
    basis: str = ""
    status: str = "verified"

    def validate(self, name: str) -> None:
        if self.min > self.max:
            raise ValueError(f"{name}: min ({self.min}) > max ({self.max})")


@dataclass(frozen=True)
class PhysicalCostParams:
    schema_version: int
    power_model_form: str
    power_model_reference: str
    p_idle_watts: NumericRange
    p_peak_watts: NumericRange
    pue: NumericRange
    wue_l_per_kwh: NumericRange
    raw: dict[str, Any]

    def sweep_corners(self) -> list[dict[str, float]]:
        """Return physically consistent corner combinations (alias of sweep)."""
        return self.sweep()

    def sweep(self) -> list[dict[str, float]]:
        """Min/max corners with P_idle <= P_peak. Water uses IT kWh × WUE, not PUE."""
        keys = ("p_idle_watts", "p_peak_watts", "pue", "wue_l_per_kwh")
        ranges = [
            (self.p_idle_watts.min, self.p_idle_watts.max),
            (self.p_peak_watts.min, self.p_peak_watts.max),
            (self.pue.min, self.pue.max),
            (self.wue_l_per_kwh.min, self.wue_l_per_kwh.max),
        ]
        corners = []
        for combo in itertools.product(*[(lo, hi) for lo, hi in ranges]):
            point = dict(zip(keys, combo))
            if point["p_idle_watts"] <= point["p_peak_watts"]:
                corners.append(point)
        return corners


def _parse_range(section: dict[str, Any], name: str) -> NumericRange:
    nr = NumericRange(
        min=float(section["min"]),
        max=float(section["max"]),
        unit=str(section.get("unit", "")),
        reference=str(section.get("reference", "")),
        basis=str(section.get("basis", "")),
        status=str(section.get("status", "verified")),
    )
    nr.validate(name)
    return nr


def load_physical_cost_params(path: Path | None = None) -> PhysicalCostParams:
    path = path or PHYSICAL_COST_TOML
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    p_idle = _parse_range(data["power_model"]["p_idle_watts"], "p_idle_watts")
    p_peak = _parse_range(data["power_model"]["p_peak_watts"], "p_peak_watts")
    if p_idle.min > p_peak.max:
        raise ValueError(
            f"P_idle range [{p_idle.min}, {p_idle.max}] is entirely above "
            f"P_peak range [{p_peak.min}, {p_peak.max}]"
        )

    pue = _parse_range(data["facility"]["pue"], "facility.pue")
    if pue.min < 1.0:
        raise ValueError("PUE must be >= 1")

    wue = _parse_range(data["water"]["wue_l_per_kwh"], "water.wue_l_per_kwh")
    if wue.min <= 0 or wue.max <= 0:
        raise ValueError("WUE must be positive")

    if "cooling_share" in data:
        raise ValueError(
            "cooling_share is removed from the registry: Green Grid WUE is already "
            "denominated in IT energy. Water = IT kWh × WUE. Use facility.pue for "
            "facility energy as a separate line item."
        )

    return PhysicalCostParams(
        schema_version=int(data.get("schema_version", 2)),
        power_model_form=str(data["power_model"]["form"]),
        power_model_reference=str(data["power_model"].get("reference", "")),
        p_idle_watts=p_idle,
        p_peak_watts=p_peak,
        pue=pue,
        wue_l_per_kwh=wue,
        raw=data,
    )


def validation_warnings(path: Path | None = None) -> list[str]:
    params = load_physical_cost_params(path)
    warnings: list[str] = []
    for label, nr in (
        ("p_idle_watts", params.p_idle_watts),
        ("p_peak_watts", params.p_peak_watts),
        ("pue", params.pue),
        ("wue_l_per_kwh", params.wue_l_per_kwh),
    ):
        if nr.status == "unverified":
            warnings.append(
                f"{label} range [{nr.min}, {nr.max}] is unverified — citation pending"
            )
    if params.p_idle_watts.max > params.p_peak_watts.min:
        warnings.append(
            "P_idle max exceeds P_peak min (envelope spans server classes); "
            "sweep() drops physically impossible corners"
        )
    green_grid = params.raw.get("water", {}).get("wue_definition", {})
    if green_grid.get("status") == "unverified":
        warnings.append("Green Grid WUE definition citation unconfirmed")
    return warnings
