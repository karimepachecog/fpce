"""Tests for the anti-leakage feature contract."""

from __future__ import annotations

import pytest

from fpce.contracts import load_feature_contract


def test_default_contract_denies_post_hoc_and_label_columns() -> None:
    contract = load_feature_contract()
    assert contract.label == "failed"
    assert contract.prediction_unit == "batch_instance"
    denied = {
        "cpu_avg",
        "end_time",
        "status",
        "failed",
        "seconds_to_next_failure",
        "failure_within_horizon",
        "job_name",
    }
    assert denied <= contract.deny
    assert "plan_cpu" in contract.allow
    assert "cpu_util_percent" in contract.allow_from_time_grid


def test_assert_no_leakage_raises() -> None:
    contract = load_feature_contract()
    with pytest.raises(ValueError, match="cpu_avg"):
        contract.assert_no_leakage(["plan_cpu", "cpu_avg"])


def test_allowed_columns_filters_and_keeps_safe() -> None:
    contract = load_feature_contract()
    kept = contract.allowed_columns(["plan_cpu", "seq_no", "machine_id", "cpu_util_percent"])
    assert kept == ["plan_cpu", "seq_no", "machine_id", "cpu_util_percent"]
