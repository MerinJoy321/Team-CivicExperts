"""Unit tests for `TelemetryModule` task/batch lifecycle hooks (Task 6.1)
and `compute_critical_path_latency` (Task 6.2).

Covers: single-task batches, overlapping-task batches (size + elapsed time),
per-category and overall `maximum_concurrency` peak tracking, sequential
(non-overlapping) tasks forming separate batches, and the defensive
assertion guarding against an unpaired `on_task_complete` call.

Also covers `compute_critical_path_latency`: a single task with no
dependencies, a linear chain, independent parallel branches converging on
a final task (the "longest chain, not sum of all" property), Property 3's
strict-less-than assertion, an empty task list, missing timestamps, and
cycle detection.

_Requirements: 2.8, 2.10, 3.6, 3.7, 4.6, 4.7, 4.8_
"""

from __future__ import annotations

import pytest

from civicpilot.scheduler.models import TaskStatus, ToolCategory, ToolTask
from civicpilot.telemetry.telemetry_module import (
    CriticalPathCycleDetectedError,
    TelemetryModule,
)


def make_task(
    task_id: str,
    category: ToolCategory,
    started_at: float,
    completed_at: float | None = None,
    depends_on: list[str] | None = None,
) -> ToolTask:
    """Builds a minimal fake `ToolTask` with fixed timestamps, so batch
    elapsed-time computations are deterministic in tests."""
    return ToolTask(
        task_id=task_id,
        category=category,
        tool_name="fake_tool",
        params={},
        priority=5,
        timeout_ms=1000,
        depends_on=depends_on if depends_on is not None else [],
        agent_role="test_agent",
        status=TaskStatus.RUNNING,
        started_at=started_at,
        completed_at=completed_at,
    )


class TestSingleTaskBatch:
    def test_single_task_start_complete_triggers_batch_of_size_one(self) -> None:
        telemetry = TelemetryModule()
        task = make_task("t1", ToolCategory.SEARCH, started_at=10.0, completed_at=12.5)

        telemetry.on_task_start(task)
        telemetry.on_task_complete(task)

        assert telemetry.parallel_batches == 1
        batches = telemetry.batches(ToolCategory.SEARCH)
        assert len(batches) == 1
        assert batches[0].batch_size == 1
        assert batches[0].elapsed_s == pytest.approx(2.5)
        assert batches[0].category == ToolCategory.SEARCH


class TestOverlappingBatch:
    def test_overlapping_tasks_form_one_batch_with_correct_size_and_elapsed(self) -> None:
        telemetry = TelemetryModule()
        t1 = make_task("t1", ToolCategory.FETCH, started_at=0.0, completed_at=5.0)
        t2 = make_task("t2", ToolCategory.FETCH, started_at=1.0, completed_at=6.0)
        t3 = make_task("t3", ToolCategory.FETCH, started_at=2.0, completed_at=7.0)

        # All three start while the category's running count is > 0
        # continuously, so they belong to the same batch.
        telemetry.on_task_start(t1)
        telemetry.on_task_start(t2)
        telemetry.on_task_start(t3)

        # Completing t1 and t2 does not drain the running count to zero yet.
        telemetry.on_task_complete(t1)
        telemetry.on_task_complete(t2)
        assert telemetry.parallel_batches == 0

        # t3 is the last to finish -> batch completes here.
        telemetry.on_task_complete(t3)

        assert telemetry.parallel_batches == 1
        batches = telemetry.batches(ToolCategory.FETCH)
        assert len(batches) == 1
        assert batches[0].batch_size == 3
        # First start (t1 @ 0.0) to last finish (t3 @ 7.0).
        assert batches[0].elapsed_s == pytest.approx(7.0)


class TestMaximumConcurrency:
    def test_maximum_concurrency_reflects_peak_overlap_per_category_and_overall(self) -> None:
        telemetry = TelemetryModule()
        t1 = make_task("t1", ToolCategory.SEARCH, started_at=0.0, completed_at=10.0)
        t2 = make_task("t2", ToolCategory.SEARCH, started_at=1.0, completed_at=2.0)
        t3 = make_task("t3", ToolCategory.SEARCH, started_at=3.0, completed_at=4.0)

        telemetry.on_task_start(t1)
        telemetry.on_task_start(t2)  # overlap of 2 with t1
        telemetry.on_task_complete(t2)
        telemetry.on_task_start(t3)  # overlap of 2 with t1 again, not 3
        telemetry.on_task_complete(t3)
        telemetry.on_task_complete(t1)

        assert telemetry.maximum_concurrency(ToolCategory.SEARCH) == 2
        assert telemetry.maximum_concurrency() == 2
        # A category never observed has a peak of 0.
        assert telemetry.maximum_concurrency(ToolCategory.FETCH) == 0


class TestSequentialTasksFormSeparateBatches:
    def test_two_sequential_non_overlapping_tasks_form_two_batches(self) -> None:
        telemetry = TelemetryModule()
        t1 = make_task("t1", ToolCategory.VERIFY, started_at=0.0, completed_at=2.0)
        t2 = make_task("t2", ToolCategory.VERIFY, started_at=5.0, completed_at=8.0)

        telemetry.on_task_start(t1)
        telemetry.on_task_complete(t1)
        telemetry.on_task_start(t2)
        telemetry.on_task_complete(t2)

        assert telemetry.parallel_batches == 2
        batches = telemetry.batches(ToolCategory.VERIFY)
        assert len(batches) == 2
        assert batches[0].batch_size == 1
        assert batches[0].elapsed_s == pytest.approx(2.0)
        assert batches[1].batch_size == 1
        assert batches[1].elapsed_s == pytest.approx(3.0)


class TestOverallCrossCategoryConcurrency:
    def test_overall_maximum_concurrency_sums_across_categories_when_overlapping(self) -> None:
        telemetry = TelemetryModule()
        search_task = make_task("s1", ToolCategory.SEARCH, started_at=0.0, completed_at=5.0)
        fetch_task = make_task("f1", ToolCategory.FETCH, started_at=1.0, completed_at=4.0)
        verify_task = make_task("v1", ToolCategory.VERIFY, started_at=2.0, completed_at=3.0)

        telemetry.on_task_start(search_task)
        telemetry.on_task_start(fetch_task)
        telemetry.on_task_start(verify_task)
        # At this point all three overlap: overall running == 3.
        telemetry.on_task_complete(verify_task)
        telemetry.on_task_complete(fetch_task)
        telemetry.on_task_complete(search_task)

        # Each individual category never exceeds 1 concurrently-running task.
        assert telemetry.maximum_concurrency(ToolCategory.SEARCH) == 1
        assert telemetry.maximum_concurrency(ToolCategory.FETCH) == 1
        assert telemetry.maximum_concurrency(ToolCategory.VERIFY) == 1
        # But combined across categories, the overall peak is 3.
        assert telemetry.maximum_concurrency() == 3


class TestDefensiveNegativeRunningCount:
    def test_on_task_complete_without_matching_start_raises_assertion_error(self) -> None:
        telemetry = TelemetryModule()
        orphan = make_task("orphan", ToolCategory.SEARCH, started_at=0.0, completed_at=1.0)

        with pytest.raises(AssertionError):
            telemetry.on_task_complete(orphan)


class TestComputeCriticalPathLatencyEmptyAndSingle:
    def test_empty_task_list_returns_zero(self) -> None:
        telemetry = TelemetryModule()
        assert telemetry.compute_critical_path_latency([]) == 0.0

    def test_single_task_with_no_dependencies_returns_its_own_duration(self) -> None:
        telemetry = TelemetryModule()
        task = make_task("t1", ToolCategory.SEARCH, started_at=0.0, completed_at=2.5)

        assert telemetry.compute_critical_path_latency([task]) == pytest.approx(2.5)


class TestComputeCriticalPathLatencyLinearChain:
    def test_linear_chain_sums_all_durations_along_the_single_chain(self) -> None:
        telemetry = TelemetryModule()
        # A -> B -> C (B depends on A, C depends on B): one continuous chain.
        a = make_task("A", ToolCategory.SEARCH, started_at=0.0, completed_at=2.0)
        b = make_task("B", ToolCategory.FETCH, started_at=2.0, completed_at=5.0, depends_on=["A"])
        c = make_task("C", ToolCategory.VERIFY, started_at=5.0, completed_at=9.0, depends_on=["B"])

        latency = telemetry.compute_critical_path_latency([a, b, c])

        # durations: A=2.0, B=3.0, C=4.0 -> chain total = 9.0
        assert latency == pytest.approx(9.0)


class TestComputeCriticalPathLatencyParallelBranches:
    def test_independent_branches_converging_use_max_not_sum(self) -> None:
        telemetry = TelemetryModule()
        # A and B are independent (no dependency between them), both feed
        # into C. Critical path = max(A, B) + C, NOT A + B + C.
        a = make_task("A", ToolCategory.SEARCH, started_at=0.0, completed_at=2.0)  # duration 2.0
        b = make_task("B", ToolCategory.SEARCH, started_at=0.0, completed_at=5.0)  # duration 5.0
        c = make_task(
            "C", ToolCategory.VERIFY, started_at=5.0, completed_at=8.0, depends_on=["A", "B"]
        )  # duration 3.0

        latency = telemetry.compute_critical_path_latency([a, b, c])

        assert latency == pytest.approx(8.0)  # max(2.0, 5.0) + 3.0
        assert latency != pytest.approx(2.0 + 5.0 + 3.0)  # not the sum of all three


class TestComputeCriticalPathLatencyPropertyThreeAssertion:
    def test_latency_strictly_less_than_sum_of_all_durations_with_independent_pair(self) -> None:
        telemetry = TelemetryModule()
        # Mirrors design's Property 3: at least one independent pair exists
        # (search tasks s1/s2 are independent), so critical path latency
        # must be strictly less than the sum of every task's duration.
        s1 = make_task("s1", ToolCategory.SEARCH, started_at=0.0, completed_at=2.0)
        s2 = make_task("s2", ToolCategory.SEARCH, started_at=0.0, completed_at=3.0)
        f1 = make_task(
            "f1", ToolCategory.FETCH, started_at=3.0, completed_at=5.0, depends_on=["s1"]
        )
        f2 = make_task(
            "f2", ToolCategory.FETCH, started_at=3.0, completed_at=6.0, depends_on=["s2"]
        )
        all_tasks = [s1, s2, f1, f2]

        latency = telemetry.compute_critical_path_latency(all_tasks)
        total_sum = sum(t.completed_at - t.started_at for t in all_tasks)

        assert latency < total_sum


class TestComputeCriticalPathLatencyMissingTimestamps:
    def test_tasks_with_missing_timestamps_treated_as_zero_duration_no_crash(self) -> None:
        telemetry = TelemetryModule()
        # A SKIPPED task never actually ran, so it has no started_at/completed_at.
        skipped = ToolTask(
            task_id="skipped",
            category=ToolCategory.SEARCH,
            tool_name="fake_tool",
            params={},
            priority=5,
            timeout_ms=1000,
            depends_on=[],
            agent_role="test_agent",
            status=TaskStatus.SKIPPED,
            started_at=None,
            completed_at=None,
        )
        downstream = make_task(
            "downstream", ToolCategory.FETCH, started_at=1.0, completed_at=4.0,
            depends_on=["skipped"],
        )

        latency = telemetry.compute_critical_path_latency([skipped, downstream])

        # skipped contributes 0.0 duration; downstream contributes 3.0.
        assert latency == pytest.approx(3.0)


class TestComputeCriticalPathLatencyIgnoresUnknownDependencies:
    def test_depends_on_entry_not_present_in_batch_is_ignored(self) -> None:
        telemetry = TelemetryModule()
        task = make_task(
            "t1", ToolCategory.SEARCH, started_at=0.0, completed_at=3.0,
            depends_on=["not-in-this-batch"],
        )

        # Should not crash, and should just use t1's own duration since
        # the referenced dependency isn't part of this batch.
        assert telemetry.compute_critical_path_latency([task]) == pytest.approx(3.0)


class TestComputeCriticalPathLatencyCycleDetection:
    def test_cyclic_depends_on_raises_clear_error_instead_of_hanging(self) -> None:
        telemetry = TelemetryModule()
        a = make_task("A", ToolCategory.SEARCH, started_at=0.0, completed_at=1.0, depends_on=["B"])
        b = make_task("B", ToolCategory.SEARCH, started_at=0.0, completed_at=1.0, depends_on=["A"])

        with pytest.raises(CriticalPathCycleDetectedError):
            telemetry.compute_critical_path_latency([a, b])
