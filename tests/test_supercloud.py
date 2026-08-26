"""Tests for Supercloud Fan-form fit (no download required)."""

from __future__ import annotations

import numpy as np
import pytest

from fpce.provenance.supercloud import fit_fan_model


def test_perfect_linear_data_recovers_idle_peak_and_r2() -> None:
    rng = np.random.default_rng(0)
    u = rng.uniform(0, 1, size=200)
    p_idle, p_peak = 50.0, 250.0
    watts = p_idle + (p_peak - p_idle) * u
    fit = fit_fan_model(u, watts)
    assert fit["r_squared"] == pytest.approx(1.0, abs=1e-9)
    assert fit["p_idle_watts"] == pytest.approx(50.0, abs=1e-6)
    assert fit["p_peak_watts"] == pytest.approx(250.0, abs=1e-6)
    assert fit["n"] == 200


def test_rejects_too_few_points() -> None:
    with pytest.raises(ValueError, match="10"):
        fit_fan_model(np.array([0.1, 0.2]), np.array([10.0, 20.0]))
