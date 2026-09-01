"""Tests for the Role C physics engine."""

import numpy as np
import pytest

from fpce.costing.translate import translate


def test_translate_zero_utilization():
    params = {
        "p_idle_watts": 100.0,
        "p_peak_watts": 300.0,
        "pue": 1.5,
        "wue_l_per_kwh": 0.5,
    }
    # 2 minutes = 120s
    dt_seconds = 120.0
    u_series = np.array([0.0, 0.0])
    
    res = translate(u_series, dt_seconds, params)
    
    # E_IT = 100W * 120s = 12000 W*s = 12000 / 3.6e6 kWh = 0.003333... kWh
    expected_it_kwh = 12000.0 / 3.6e6
    assert res.it_kwh == pytest.approx(expected_it_kwh)
    assert res.facility_kwh == pytest.approx(expected_it_kwh * 1.5)
    assert res.water_liters == pytest.approx(expected_it_kwh * 0.5)
    assert res.u_mean == 0.0
    assert res.dt_covered_seconds == 120.0


def test_translate_full_utilization():
    params = {
        "p_idle_watts": 100.0,
        "p_peak_watts": 300.0,
        "pue": 1.5,
        "wue_l_per_kwh": 0.5,
    }
    # 2 minutes = 120s
    dt_seconds = 120.0
    u_series = np.array([1.0, 1.0])
    
    res = translate(u_series, dt_seconds, params)
    
    # E_IT = 300W * 120s = 36000 W*s = 36000 / 3.6e6 kWh = 0.01 kWh
    expected_it_kwh = 36000.0 / 3.6e6
    assert res.it_kwh == pytest.approx(expected_it_kwh)
    assert res.u_mean == 1.0


def test_translate_partial_last_minute():
    params = {
        "p_idle_watts": 100.0,
        "p_peak_watts": 300.0,
        "pue": 1.0,
        "wue_l_per_kwh": 1.0,
    }
    # 90 seconds = 60s for slot 0 + 30s for slot 1
    dt_seconds = 90.0
    u_series = np.array([0.5, 1.0])
    
    res = translate(u_series, dt_seconds, params)
    
    # slot 0: P = 100 + 200*0.5 = 200W. weight = 60s. energy = 12000 W*s
    # slot 1: P = 100 + 200*1.0 = 300W. weight = 30s. energy = 9000 W*s
    # total energy = 21000 W*s = 21000 / 3.6e6 kWh
    expected_it_kwh = 21000.0 / 3.6e6
    assert res.it_kwh == pytest.approx(expected_it_kwh)
    
    # mean utilization = (0.5 * 60 + 1.0 * 30) / 90 = 60 / 90 = 0.666...
    assert res.u_mean == pytest.approx(60.0 / 90.0)
    assert res.dt_covered_seconds == 90.0


def test_translate_hold_strategy():
    params = {
        "p_idle_watts": 100.0,
        "p_peak_watts": 300.0,
        "pue": 1.0,
        "wue_l_per_kwh": 1.0,
    }
    # 75 seconds but only one slot in series.
    # We hold the value for 75s.
    dt_seconds = 75.0
    u_series = np.array([0.5])
    
    res = translate(u_series, dt_seconds, params)
    
    # slot 0: P = 200W. weight = 75s. energy = 15000 W*s
    expected_it_kwh = 15000.0 / 3.6e6
    assert res.it_kwh == pytest.approx(expected_it_kwh)
    assert res.u_mean == 0.5


def test_translate_zero_time():
    params = {
        "p_idle_watts": 100.0,
        "p_peak_watts": 300.0,
        "pue": 1.0,
        "wue_l_per_kwh": 1.0,
    }
    res = translate(np.array([1.0]), 0.0, params)
    assert res.it_kwh == 0.0
    assert res.dt_covered_seconds == 0.0


def test_translate_clipping():
    params = {
        "p_idle_watts": 100.0,
        "p_peak_watts": 300.0,
        "pue": 1.0,
        "wue_l_per_kwh": 1.0,
    }
    # value > 1 should be clipped to 1.0
    dt_seconds = 60.0
    u_series = np.array([1.01])
    
    res = translate(u_series, dt_seconds, params)
    # clipped to 1.0 -> 300W * 60s = 18000 W*s
    expected_it_kwh = 18000.0 / 3.6e6
    assert res.it_kwh == pytest.approx(expected_it_kwh)
