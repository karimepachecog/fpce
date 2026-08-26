"""Tests for SPECpower parsing, envelopes, and TOML provenance lock."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from fpce.config import PHYSICAL_COST_TOML
from fpce.costing.coefficients import load_physical_cost_params
from fpce.provenance.specpower import (
    DEFAULT_OUTPUT,
    envelope_matches_toml,
    filter_matched_systems,
    parse_result_txt,
    summarize_matched_envelope,
    summarize_power_envelope,
)

FIXTURE = Path(__file__).parent / "fixtures" / "specpower_multinode.txt"


def test_parse_multinode_normalizes_watts_per_node() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    row = parse_result_txt(text, "https://example.test/multi.txt")
    assert row is not None
    assert row["identical_nodes"] == 4
    assert row["chips"] == 2
    assert row["cores_enabled"] == 56
    assert row["hardware_threads"] == 112
    assert row["memory_gb"] == 192.0
    assert row["watts_100"] == pytest.approx(1417.0)
    assert row["watts_100_per_node"] == pytest.approx(1417.0 / 4)
    assert row["watts_Active_Idle_per_node"] == pytest.approx(193.0 / 4)


def _toy_curves() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "hardware_threads": [96, 96, 112, 48],
            "chips": [2, 2, 2, 1],
            "watts_Active_Idle_per_node": [80.0, 90.0, 40.0, 30.0],
            "watts_100_per_node": [200.0, 220.0, 400.0, 120.0],
            "watts_Active_Idle": [80.0, 90.0, 40.0, 30.0],
            "watts_100": [200.0, 220.0, 400.0, 120.0],
        }
    )


def test_generic_envelope_uses_per_node_watts() -> None:
    env = summarize_power_envelope(_toy_curves())
    assert env["p_idle_min"] == 30.0
    assert env["p_idle_max"] == 90.0
    assert env["p_peak_min"] == 120.0
    assert env["p_peak_max"] == 400.0
    assert env["n_systems"] == 4


def test_matched_envelope_filters_96_thread_2_chip() -> None:
    matched = filter_matched_systems(_toy_curves(), threads=96, tolerance=0.1, chips=2)
    assert len(matched) == 2
    env = summarize_matched_envelope(_toy_curves(), min_systems=2)
    assert env["credible"] is True
    assert env["p_idle_min"] == 80.0
    assert env["p_idle_max"] == 90.0
    assert env["p_peak_min"] == 200.0
    assert env["p_peak_max"] == 220.0
    assert env["n_systems"] == 2


def test_envelope_matches_toml_tolerance() -> None:
    env = {"p_idle_min": 80.04, "p_idle_max": 90.0, "p_peak_min": 200.0, "p_peak_max": 220.0}
    assert envelope_matches_toml(env, 80.0, 90.0, 200.0, 220.0)
    assert not envelope_matches_toml(env, 10.0, 90.0, 200.0, 220.0)


@pytest.mark.skipif(not DEFAULT_OUTPUT.exists(), reason="SPEC parquet not scraped yet")
def test_toml_locked_to_scraped_envelope() -> None:
    curves = pd.read_parquet(DEFAULT_OUTPUT)
    matched = summarize_matched_envelope(curves)
    chosen = matched if matched.get("credible") else summarize_power_envelope(curves)
    params = load_physical_cost_params(PHYSICAL_COST_TOML)
    assert envelope_matches_toml(
        chosen,
        params.p_idle_watts.min,
        params.p_idle_watts.max,
        params.p_peak_watts.min,
        params.p_peak_watts.max,
    ), (
        f"TOML idle=[{params.p_idle_watts.min}, {params.p_idle_watts.max}] "
        f"peak=[{params.p_peak_watts.min}, {params.p_peak_watts.max}] "
        f"envelope idle=[{chosen['p_idle_min']:.1f}, {chosen['p_idle_max']:.1f}] "
        f"peak=[{chosen['p_peak_min']:.1f}, {chosen['p_peak_max']:.1f}] "
        f"n={chosen['n_systems']} kind={'matched' if matched.get('credible') else 'generic'}"
    )
