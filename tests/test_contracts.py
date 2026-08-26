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
    assert "plan_cpu_frac" in contract.allow
    assert "attempt_index" in contract.allow
    assert "retry_index" in contract.allow
    assert "cpu_util_percent" in contract.allow_from_time_grid
    assert "waste_window_upper_bound_seconds" in contract.deny
    assert "terminal_type" in contract.deny
    assert "start_imputed" in contract.deny
    assert "sched_time" in contract.deny
    assert "submit_time" in contract.deny
    assert "terminal_code" in contract.deny


def test_assert_no_leakage_raises() -> None:
    contract = load_feature_contract()
    with pytest.raises(ValueError, match="cpu_avg"):
        contract.assert_no_leakage(["plan_cpu", "cpu_avg"])


def test_allowed_columns_filters_and_keeps_safe() -> None:
    contract = load_feature_contract()
    kept = contract.allowed_columns(["plan_cpu", "seq_no", "machine_id", "cpu_util_percent"])
    assert kept == ["plan_cpu", "seq_no", "machine_id", "cpu_util_percent"]


def test_repo_relpath_is_relative_to_project_root() -> None:
    from fpce.config import DATA_PROCESSED, PROJECT_ROOT, repo_relpath, resolve_repo_path

    rel = repo_relpath(DATA_PROCESSED / "primary" / "instance_events.parquet")
    assert rel == "data/processed/primary/instance_events.parquet"
    assert not rel.startswith("/")
    assert resolve_repo_path(rel) == PROJECT_ROOT / rel


def test_tracked_handoff_json_paths_are_repo_relative() -> None:
    import json
    from pathlib import Path

    from fpce.config import PROJECT_ROOT

    split = json.loads(
        (PROJECT_ROOT / "data/processed/primary_time_split.json").read_text()
    )
    manifest = json.loads(
        (PROJECT_ROOT / "data/processed/google/export_manifest.json").read_text()
    )
    quality = json.loads((PROJECT_ROOT / "reports/google_quality.json").read_text())
    supercloud = json.loads(
        (PROJECT_ROOT / "reports/supercloud_fan_fit.json").read_text()
    )
    for value in (
        split["grid_path"],
        split["instance_events_path"],
        manifest["output"],
        quality["attempts_path"],
        quality["manifest"]["output"],
        supercloud["source"],
    ):
        assert not Path(value).is_absolute(), value
        assert not str(value).startswith("/"), value
