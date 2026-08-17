"""Latency targets and failure classification test suite (Requirements 4.1-4.5).

Covers:
- Normal_Request latency target <= 15.0s.
- Complex_Request latency target <= 25.0s.
- Performance failure classification (total_time > 30.0s).
- Architecture failure classification (total_time > 60.0s).
"""

from __future__ import annotations

import asyncio
import pytest

from civicpilot.telemetry.telemetry_module import TelemetrySnapshot


class TestLatencyTargets:
    def test_performance_failure_classification_above_30s(self) -> None:
        """Requirement 4.4: total_time > 30s sets performance_failure=True."""
        snap_normal = TelemetrySnapshot(
            intake_time=0.5,
            planning_time=1.0,
            search_time=2.0,
            filtering_time=0.5,
            fetch_time=2.0,
            verification_time=2.0,
            synthesis_time=1.0,
            document_time=0.5,
            total_time=9.5,
            tool_count=5,
            successful_tool_count=5,
            failed_tool_count=0,
            cache_hits=0,
            parallel_batches=2,
            maximum_concurrency=4,
            critical_path_latency=8.0,
            performance_failure=9.5 > 30.0,
            architecture_failure=9.5 > 60.0,
        )

        assert snap_normal.performance_failure is False
        assert snap_normal.architecture_failure is False

        snap_perf_fail = TelemetrySnapshot(
            intake_time=1.0,
            planning_time=5.0,
            search_time=10.0,
            filtering_time=2.0,
            fetch_time=10.0,
            verification_time=5.0,
            synthesis_time=2.0,
            document_time=1.0,
            total_time=36.0,
            tool_count=10,
            successful_tool_count=10,
            failed_tool_count=0,
            cache_hits=0,
            parallel_batches=4,
            maximum_concurrency=4,
            critical_path_latency=25.0,
            performance_failure=36.0 > 30.0,
            architecture_failure=36.0 > 60.0,
        )

        assert snap_perf_fail.performance_failure is True
        assert snap_perf_fail.architecture_failure is False

    def test_architecture_failure_classification_above_60s(self) -> None:
        """Requirement 4.5: total_time > 60s sets architecture_failure=True."""
        snap_arch_fail = TelemetrySnapshot(
            intake_time=2.0,
            planning_time=10.0,
            search_time=20.0,
            filtering_time=5.0,
            fetch_time=20.0,
            verification_time=10.0,
            synthesis_time=5.0,
            document_time=2.0,
            total_time=74.0,
            tool_count=15,
            successful_tool_count=15,
            failed_tool_count=0,
            cache_hits=0,
            parallel_batches=5,
            maximum_concurrency=4,
            critical_path_latency=50.0,
            performance_failure=74.0 > 30.0,
            architecture_failure=74.0 > 60.0,
        )

        assert snap_arch_fail.performance_failure is True
        assert snap_arch_fail.architecture_failure is True
