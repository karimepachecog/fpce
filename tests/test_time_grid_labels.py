"""Unit tests for time_grid failure labeling (no pipeline data required)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fpce.config import FAILURE_HORIZON_SECONDS, FAILURE_STATUSES
from fpce.ingest.time_grid import (
    count_active_at_times,
    failure_timestamp,
    instance_end,
)


class TestFailureTimestamp:
    def test_failed_with_end_time(self) -> None:
        row = pd.Series({"status": "Failed", "start_time": 100, "end_time": 200})
        assert failure_timestamp(row) == 200

    def test_failed_end_time_zero_uses_start_time(self) -> None:
        row = pd.Series({"status": "Failed", "start_time": 100, "end_time": 0})
        assert failure_timestamp(row) == 100

    def test_interrupted_is_failure(self) -> None:
        row = pd.Series({"status": "Interrupted", "start_time": 50, "end_time": 0})
        assert failure_timestamp(row) == 50

    def test_terminated_is_not_failure(self) -> None:
        row = pd.Series({"status": "Terminated", "start_time": 100, "end_time": 200})
        assert failure_timestamp(row) is None

    def test_waiting_is_not_failure(self) -> None:
        row = pd.Series({"status": "Waiting", "start_time": 100, "end_time": 0})
        assert failure_timestamp(row) is None

    def test_failed_without_timestamps_returns_none(self) -> None:
        row = pd.Series({"status": "Failed", "start_time": 0, "end_time": 0})
        assert failure_timestamp(row) is None


class TestInstanceEnd:
    def test_running_open_ended_extends_to_trace_max(self) -> None:
        row = pd.Series({"status": "Running", "start_time": 100, "end_time": 0})
        assert instance_end(row, trace_max=1000) == 1000.0

    def test_failed_open_ended_ends_at_failure_proxy(self) -> None:
        row = pd.Series({"status": "Failed", "start_time": 300, "end_time": 0})
        assert instance_end(row, trace_max=1000) == 300.0


class TestCountActive:
    def test_empty_instances(self) -> None:
        times = np.array([0, 60, 120])
        counts = count_active_at_times(times, np.array([]), np.array([]))
        np.testing.assert_array_equal(counts, [0, 0, 0])

    def test_single_active_interval(self) -> None:
        times = np.array([0, 60, 120, 180])
        starts = np.array([60.0])
        ends = np.array([120.0])
        counts = count_active_at_times(times, starts, ends)
        np.testing.assert_array_equal(counts, [0, 1, 1, 0])


class TestConfigSemantics:
    def test_failure_statuses_exclude_success_and_pending(self) -> None:
        assert FAILURE_STATUSES == {"Failed", "Interrupted"}
        assert "Terminated" not in FAILURE_STATUSES
        assert "Waiting" not in FAILURE_STATUSES

    def test_horizon_is_thirty_minutes(self) -> None:
        assert FAILURE_HORIZON_SECONDS == 30 * 60
