"""Mandatory concurrency verification tests (Requirement 3 -- BLOCKING GATE).

- Task 7.1: Concurrency Test A: 5-task ~2-3s proof (Requirements 3.1-3.3, 3.7)
- Task 7.2: Concurrency Test B: 4 search / 4 fetch / 3 verify dependency chain (Requirements 3.4-3.7)

These tests form a required blocking gate: Phase 3 and all subsequent tasks
MUST NOT begin until both tests pass cleanly.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from civicpilot.scheduler.models import ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.telemetry.telemetry_module import TelemetryModule


async def _fake_executor(task: ToolTask) -> str:
    duration = task.params.get("duration", 0.0)
    if duration > 0:
        await asyncio.sleep(duration)
    return "ok"


def _make_task(
    task_id: str,
    category: ToolCategory,
    duration: float,
    depends_on: list[str] | None = None,
) -> ToolTask:
    return ToolTask(
        task_id=task_id,
        category=category,
        tool_name="fake_tool",
        params={"duration": duration},
        priority=5,
        timeout_ms=10000,
        depends_on=depends_on or [],
        agent_role="test_agent",
    )


@pytest.mark.asyncio
async def test_five_independent_tasks_run_concurrently():
    # Feature: civicpilot, Concurrency Test A (Requirement 3.1-3.3, 3.7)
    telemetry = TelemetryModule()
    scheduler = Scheduler(
        concurrency_limits={ToolCategory.SEARCH: 5},
        executor=_fake_executor,
        telemetry=telemetry,
    )

    tasks = [_make_task(f"task_{i}", ToolCategory.SEARCH, duration=2.0) for i in range(5)]

    run_loop = asyncio.create_task(scheduler.run())
    try:
        start_time = time.monotonic()
        futures = [await scheduler.submit(t) for t in tasks]
        finished_tasks = await asyncio.wait_for(asyncio.gather(*futures), timeout=5.0)
        elapsed = time.monotonic() - start_time
    finally:
        scheduler.stop()
        await run_loop

    # Requirement 3.1, 3.2: 5 tasks of 2.0s duration execute concurrently in <= 3.0s (built-in ±0.2s tolerance)
    assert elapsed <= 3.0, f"Expected elapsed time <= 3.0s, got {elapsed:.2f}s"

    # Requirement 3.3: all 5 completed successfully
    assert len(finished_tasks) == 5
    assert all(t.result == "ok" for t in finished_tasks)

    # Requirement 3.7: Telemetry reports 5 maximum overlapping tasks
    assert telemetry.maximum_concurrency() == 5, f"Expected peak concurrency 5, got {telemetry.maximum_concurrency()}"


@pytest.mark.asyncio
async def test_search_fetch_verify_dependency_chain_critical_path():
    # Feature: civicpilot, Concurrency Test B (Requirement 3.4-3.7)
    telemetry = TelemetryModule()
    scheduler = Scheduler(
        concurrency_limits={
            ToolCategory.SEARCH: 4,
            ToolCategory.FETCH: 4,
            ToolCategory.VERIFY: 3,
        },
        executor=_fake_executor,
        telemetry=telemetry,
    )

    # 4 search tasks (durations 1.0s, 1.2s, 1.4s, 1.6s)
    search_durations = [1.0, 1.2, 1.4, 1.6]
    searches = [
        _make_task(f"s{i}", ToolCategory.SEARCH, duration=d)
        for i, d in enumerate(search_durations, start=1)
    ]

    # 4 fetch tasks, each depending on its corresponding search task (durations 1.1s, 1.3s, 1.5s, 1.7s)
    fetch_durations = [1.1, 1.3, 1.5, 1.7]
    fetches = [
        _make_task(f"f{i}", ToolCategory.FETCH, duration=d, depends_on=[f"s{i}"])
        for i, d in enumerate(fetch_durations, start=1)
    ]

    # 3 verify tasks, depending on fetch tasks (durations 1.0s, 1.2s, 1.5s)
    # v1 depends on [f1], v2 depends on [f1, f2], v3 depends on [f1, f2, f3, f4]
    verifies = [
        _make_task("v1", ToolCategory.VERIFY, duration=1.0, depends_on=["f1"]),
        _make_task("v2", ToolCategory.VERIFY, duration=1.2, depends_on=["f1", "f2"]),
        _make_task("v3", ToolCategory.VERIFY, duration=1.5, depends_on=["f1", "f2", "f3", "f4"]),
    ]

    all_tasks = searches + fetches + verifies

    run_loop = asyncio.create_task(scheduler.run())
    try:
        start_time = time.monotonic()
        futures = [await scheduler.submit(t) for t in all_tasks]
        finished_tasks = await asyncio.wait_for(asyncio.gather(*futures), timeout=15.0)
        elapsed = time.monotonic() - start_time
    finally:
        scheduler.stop()
        await run_loop

    # Verify all 11 tasks completed
    assert len(finished_tasks) == 11
    assert all(t.result == "ok" for t in finished_tasks)

    # Calculate theoretical critical path latency and total sum of durations
    theoretical_critical_path = telemetry.compute_critical_path_latency(finished_tasks)
    total_sum = sum(t.params["duration"] for t in all_tasks)

    # Requirement 3.6: elapsed wall-clock time does not exceed min(1.2 * critical_path, 0.8 * sum_of_all_11_durations)
    threshold = min(1.2 * theoretical_critical_path, 0.8 * total_sum)
    assert elapsed <= threshold, (
        f"Measured elapsed wall-clock time ({elapsed:.2f}s) exceeded threshold ({threshold:.2f}s). "
        f"Critical path: {theoretical_critical_path:.2f}s, Total sum: {total_sum:.2f}s"
    )

    # Requirement 3.7: Telemetry reports at least 4 maximum overlapping tasks during search/fetch stages
    assert telemetry.maximum_concurrency() >= 4, (
        f"Expected maximum concurrency >= 4, got {telemetry.maximum_concurrency()}"
    )
