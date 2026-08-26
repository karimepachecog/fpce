"""Load and validate the physical cost coefficient registry."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

from fpce.config import DATA_PROCESSED, PHYSICAL_COST_TOML


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


@dataclass(frozen=True)
class OperatorProfile:
    """One operator-declared PUE/WUE point. Missing values stay None."""

    id: str
    name: str
    year: int
    pue: float | None
    wue_l_per_kwh: float | None
    scope: str
    reference: str
    url: str
    notes: str


def load_operator_profiles(path: Path | None = None) -> list[OperatorProfile]:
    """ESG point values from [[operators]] in the coefficient registry."""
    path = path or PHYSICAL_COST_TOML
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    profiles: list[OperatorProfile] = []
    for row in data.get("operators", []):
        pue = row.get("pue")
        wue = row.get("wue_l_per_kwh")
        if pue is not None and float(pue) < 1.0:
            raise ValueError(f"operators.{row.get('id')}: PUE must be >= 1")
        if wue is not None and float(wue) <= 0:
            raise ValueError(f"operators.{row.get('id')}: WUE must be positive")
        profiles.append(
            OperatorProfile(
                id=str(row["id"]),
                name=str(row["name"]),
                year=int(row["year"]),
                pue=None if pue is None else float(pue),
                wue_l_per_kwh=None if wue is None else float(wue),
                scope=str(row.get("scope", "")),
                reference=str(row.get("reference", "")),
                url=str(row.get("url", "")),
                notes=str(row.get("notes", "")),
            )
        )
    return profiles


def operator_scale_vs_national(path: Path | None = None) -> dict[str, Any]:
    """How water and facility energy scale vs LBNL for the same IT kWh.

    Does not integrate Fan et al. power. Role C still owns kWh/liter ranges.
    """
    params = load_physical_cost_params(path)
    profiles = load_operator_profiles(path)
    operators = []
    for op in profiles:
        entry: dict[str, Any] = {
            "id": op.id,
            "name": op.name,
            "year": op.year,
            "pue": op.pue,
            "wue_l_per_kwh": op.wue_l_per_kwh,
            "scope": op.scope,
            "reference": op.reference,
            "url": op.url,
            "notes": op.notes,
        }
        if op.pue is not None:
            entry["facility_kwh_scale_vs_lbnl"] = {
                "vs_min": round(op.pue / params.pue.min, 4),
                "vs_max": round(op.pue / params.pue.max, 4),
            }
        if op.wue_l_per_kwh is not None:
            entry["water_liters_scale_vs_lbnl"] = {
                "vs_min": round(op.wue_l_per_kwh / params.wue_l_per_kwh.min, 4),
                "vs_max": round(op.wue_l_per_kwh / params.wue_l_per_kwh.max, 4),
            }
        operators.append(entry)
    return {
        "notes": (
            "For a fixed IT kWh, facility energy scales with PUE and water with WUE. "
            "vs_min / vs_max are operator_value / LBNL_range_endpoint. "
            "This is not a Fan et al. costing run and not ground-truth validation."
        ),
        "lbnl": {
            "pue_min": params.pue.min,
            "pue_max": params.pue.max,
            "wue_l_per_kwh_min": params.wue_l_per_kwh.min,
            "wue_l_per_kwh_max": params.wue_l_per_kwh.max,
            "reference_pue": params.pue.reference,
            "reference_wue": params.wue_l_per_kwh.reference,
        },
        "operators": operators,
    }


def write_operator_scale_report(output: Path | None = None) -> Path:
    from fpce.config import REPORTS_DIR, repo_relpath

    dest = output or (REPORTS_DIR / "operator_coefficient_scale.json")
    payload = operator_scale_vs_national()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] operator scale -> {repo_relpath(dest)}")
    return dest


def main() -> None:
    write_operator_scale_report()


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

    parquet = DATA_PROCESSED / "spec_power_curves.parquet"
    if not parquet.exists():
        warnings.append(
            f"SPEC curves parquet missing ({parquet}); "
            "cannot verify TOML P_idle/P_peak against summarize_*_envelope()"
        )
        return warnings
    try:
        import pandas as pd

        from fpce.provenance.specpower import (
            envelope_matches_toml,
            summarize_matched_envelope,
            summarize_power_envelope,
        )

        curves = pd.read_parquet(parquet)
        matched = summarize_matched_envelope(curves)
        chosen = matched if matched.get("credible") else summarize_power_envelope(curves)
        if not envelope_matches_toml(
            chosen,
            params.p_idle_watts.min,
            params.p_idle_watts.max,
            params.p_peak_watts.min,
            params.p_peak_watts.max,
        ):
            warnings.append(
                "physical_cost.toml P_idle/P_peak do not match "
                f"the SPEC parquet envelope "
                f"(idle [{chosen['p_idle_min']:.1f}, {chosen['p_idle_max']:.1f}], "
                f"peak [{chosen['p_peak_min']:.1f}, {chosen['p_peak_max']:.1f}], "
                f"n={chosen['n_systems']}, kind="
                f"{'matched' if matched.get('credible') else 'generic'})"
            )
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(f"could not verify SPEC envelope against TOML: {exc}")
    return warnings


if __name__ == "__main__":
    main()
