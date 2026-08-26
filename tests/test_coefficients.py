"""Tests for physical cost coefficient registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from fpce.costing.coefficients import load_physical_cost_params, validation_warnings
from fpce.config import PHYSICAL_COST_TOML


def _minimal_toml(
    *,
    idle_min=80,
    idle_max=220,
    peak_min=150,
    peak_max=450,
    pue_min=1.15,
    pue_max=1.40,
    extra="",
) -> str:
    return f"""
schema_version = 2
[power_model]
form = "test"
[power_model.p_idle_watts]
min = {idle_min}
max = {idle_max}
unit = "W"
[power_model.p_peak_watts]
min = {peak_min}
max = {peak_max}
unit = "W"
[facility.pue]
min = {pue_min}
max = {pue_max}
unit = "x"
[water.wue_l_per_kwh]
min = 0.45
max = 0.48
unit = "L/kWh"
{extra}
"""


def test_load_default_params() -> None:
    params = load_physical_cost_params()
    assert params.p_idle_watts.min <= params.p_idle_watts.max
    assert params.p_peak_watts.min <= params.p_peak_watts.max
    assert params.pue.min >= 1.0
    assert params.wue_l_per_kwh.min > 0
    assert "cooling_share" not in params.raw


def test_operator_profiles_are_cited_and_scale_vs_lbnl() -> None:
    from fpce.costing.coefficients import (
        load_operator_profiles,
        operator_scale_vs_national,
    )

    profiles = load_operator_profiles()
    ids = {p.id for p in profiles}
    assert {"google_2023", "microsoft_fy24", "meta_2023", "equinix_2024", "aws_2024"} <= ids
    google = next(p for p in profiles if p.id == "google_2023")
    assert google.pue == 1.10
    assert google.wue_l_per_kwh is None
    assert google.reference
    meta = next(p for p in profiles if p.id == "meta_2023")
    assert meta.wue_l_per_kwh == 0.18
    report = operator_scale_vs_national()
    by_id = {row["id"]: row for row in report["operators"]}
    assert "facility_kwh_scale_vs_lbnl" in by_id["google_2023"]
    assert "water_liters_scale_vs_lbnl" not in by_id["google_2023"]
    water = by_id["meta_2023"]["water_liters_scale_vs_lbnl"]
    assert water["vs_min"] == pytest.approx(0.18 / 0.45, rel=1e-3)
    assert water["vs_max"] == pytest.approx(0.18 / 0.48, rel=1e-3)


def test_sweep_drops_idle_above_peak() -> None:
    params = load_physical_cost_params()
    corners = params.sweep()
    assert corners
    assert all(c["p_idle_watts"] <= c["p_peak_watts"] for c in corners)
    n_unfiltered = 16
    dropped = 4 if params.p_idle_watts.max > params.p_peak_watts.min else 0
    assert len(corners) == n_unfiltered - dropped


def test_pue_and_wue_are_independent_sweep_axes() -> None:
    params = load_physical_cost_params()
    keys = params.sweep()[0].keys()
    assert "pue" in keys
    assert "wue_l_per_kwh" in keys
    assert "cooling_share" not in keys


def test_cooling_share_section_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(_minimal_toml(extra="[cooling_share]\nmin=0.3\nmax=0.4\n"), encoding="utf-8")
    with pytest.raises(ValueError, match="cooling_share"):
        load_physical_cost_params(path)


def test_invalid_range_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(_minimal_toml(idle_min=100, idle_max=50), encoding="utf-8")
    with pytest.raises(ValueError, match="min"):
        load_physical_cost_params(path)


def test_pue_below_one_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(_minimal_toml(pue_min=0.8, pue_max=1.2), encoding="utf-8")
    with pytest.raises(ValueError, match="PUE"):
        load_physical_cost_params(path)


def test_no_unverified_warnings_on_default_registry() -> None:
    warnings = validation_warnings(PHYSICAL_COST_TOML)
    assert not any("unverified" in w.lower() for w in warnings)
    assert not any("cooling_share" in w for w in warnings)
