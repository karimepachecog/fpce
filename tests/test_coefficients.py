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


def test_sweep_drops_idle_above_peak() -> None:
    params = load_physical_cost_params()
    corners = params.sweep()
    assert corners
    assert all(c["p_idle_watts"] <= c["p_peak_watts"] for c in corners)
    # Unfiltered 2^4 = 16; (idle_max, peak_min) = (220, 150) is invalid → 12
    assert len(corners) == 12


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
