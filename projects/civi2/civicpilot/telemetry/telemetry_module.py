"""`TelemetryModule` task/batch lifecycle hooks (Requirements 2.8, 2.10, 3.7,
4.6, 4.7) plus Critical_Path_Latency computation (Requirements 3.6, 4.8).

This module implements the task/batch lifecycle portion of the design's
`TelemetryModule` -- `on_task_start`, `on_task_complete`, `on_batch_complete`
-- plus the "maximum observed overlapping tasks" tracking used by
Requirement 3.7/4.7 (`maximum_concurrency`, `parallel_batches`), and
`compute_critical_path_latency` (Task 6.2). `snapshot` is the responsibility
of a later task and is intentionally NOT defined here, to avoid colliding
with that concurrently-developed code.

Hook contract
-------------
`on_task_start`/`on_task_complete` are expected to be invoked synchronously,
in real chronological order, at the exact moment a `ToolTask` transitions to
`RUNNING` and to a terminal status respectively (the Scheduler wiring that
calls these hooks at the right points lands in Task 6.3 -- this module is
testable in isolation by calling the hooks directly with constructed
`ToolTask` objects). Each hook prefers the task's own `started_at`/
`completed_at` timestamp when present (so callers/tests can construct
fully-timestamped fake tasks and get deterministic elapsed-time results),
falling back to `now_s()` at call time otherwise.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

from civicpilot.scheduler.models import ToolCategory, ToolTask
from civicpilot.telemetry.timing import elapsed_s, now_s

from dataclasses import field

__all__ = [
    "BatchRecord",
    "TelemetryModule",
    "TelemetrySnapshot",
    "CriticalPathCycleDetectedError",
]


@dataclass
class TelemetrySnapshot:
    """Per-request telemetry snapshot (Requirements 4.4-4.8)."""

    intake_time: float
    planning_time: float
    search_time: float
    filtering_time: float
    fetch_time: float
    verification_time: float
    synthesis_time: float
    document_time: float
    total_time: float
    tool_count: int
    successful_tool_count: int
    failed_tool_count: int
    cache_hits: int
    parallel_batches: int
    maximum_concurrency: int
    critical_path_latency: float
    performance_failure: bool
    architecture_failure: bool
    trace_events: list[Any] = field(default_factory=list)


class CriticalPathCycleDetectedError(Exception):
    """Raised by `compute_critical_path_latency` if the `depends_on` edges
    among the supplied tasks (restricted to task_ids present in that same
    list) form a cycle.

    A cyclic dependency graph has no well-defined longest root-to-leaf
    path. The Scheduler's own `DependencyGraph` (Task 3.2) already rejects
    cycles before a task set is ever submitted, so this exists purely as
    a defensive guard -- mirroring the cycle-detection style used by
    `civicpilot.scheduler.dependency_graph.DependencyGraph._check_for_cycles`
    -- rather than an expected runtime path.
    """


@dataclass
class BatchRecord:
    """A single recorded batch (Requirement 2.10): the number of tasks that
    executed concurrently within a tool category, and the elapsed time from
    the first task's start in the batch to the last task's completion.
    """

    category: ToolCategory
    batch_size: int
    elapsed_s: float


class TelemetryModule:
    """Records task/batch lifecycle events and concurrency-peak metrics.

    Per Requirement 2.8, this class is the target of every task lifecycle
    transition. Per Requirement 2.10, it records each category's completed
    concurrent batches (size + elapsed time). Per Requirement 3.7/4.7, it
    tracks the maximum number of tasks observed with overlapping execution
    windows, both per category and overall (the peak, across all points in
    time, of the *combined* concurrently-running count across every
    category).
    """

    def __init__(self) -> None:
        # Current number of RUNNING tasks, per category and combined overall.
        self._running_count: dict[ToolCategory, int] = defaultdict(int)
        self._overall_running: int = 0

        # Peak (maximum-ever-observed) concurrency, per category and overall.
        self._max_concurrency_per_category: dict[ToolCategory, int] = defaultdict(int)
        self._max_concurrency_overall: int = 0

        # Per-category "current batch" accumulators: the number of tasks
        # that have started since the category's running count last hit
        # zero, and the timestamp of the first such start.
        self._batch_task_count: dict[ToolCategory, int] = defaultdict(int)
        self._batch_first_start_at: dict[ToolCategory, Optional[float]] = defaultdict(lambda: None)

        # Recorded batches, for later inclusion in a TelemetrySnapshot.
        self._batches: list[BatchRecord] = []
        self._batches_by_category: dict[ToolCategory, list[BatchRecord]] = defaultdict(list)
        self._parallel_batches: int = 0

    def on_task_start(self, task: ToolTask) -> None:
        """Records that `task`'s category now has one more concurrently-
        running task, and updates the maximum-observed-overlap tracking for
        that category and overall (Requirement 3.7, 4.7).
        """
        category = task.category
        start_ts = task.started_at if task.started_at is not None else now_s()

        if self._running_count[category] == 0:
            # First start of a new batch for this category.
            self._batch_first_start_at[category] = start_ts
            self._batch_task_count[category] = 0
        self._batch_task_count[category] += 1

        self._running_count[category] += 1
        self._overall_running += 1

        if self._running_count[category] > self._max_concurrency_per_category[category]:
            self._max_concurrency_per_category[category] = self._running_count[category]
        if self._overall_running > self._max_concurrency_overall:
            self._max_concurrency_overall = self._overall_running

    def on_task_complete(self, task: ToolTask) -> None:
        """Records that `task`'s category now has one fewer concurrently-
        running task. If the category's running count just drained to zero,
        this signals the end of a batch (Requirement 2.10):
        `on_batch_complete` is invoked with the batch size accumulated since
        the batch started and the elapsed time from the batch's first start
        to this completion, and the batch trackers are reset for the next
        batch.

        Raises `AssertionError` if called for a category whose running
        count is already 0 -- this should never happen if `on_task_start`/
        `on_task_complete` calls are always paired correctly, and a negative
        running count would indicate a caller bug rather than a valid state.
        """
        category = task.category

        if self._running_count[category] <= 0:
            raise AssertionError(
                f"on_task_complete called for category {category!r} whose "
                "running count is already 0 -- this indicates unpaired "
                "on_task_start/on_task_complete calls (a caller bug); "
                "refusing to let the running count go negative"
            )

        end_ts = task.completed_at if task.completed_at is not None else now_s()

        self._running_count[category] -= 1
        self._overall_running -= 1

        if self._running_count[category] == 0:
            batch_size = self._batch_task_count[category]
            batch_start = self._batch_first_start_at[category]
            assert batch_start is not None  # invariant: set whenever count went 0 -> 1

            batch_elapsed = elapsed_s(batch_start, end_ts)
            self.on_batch_complete(category, batch_size, batch_elapsed)

            # Reset for the next batch in this category.
            self._batch_task_count[category] = 0
            self._batch_first_start_at[category] = None

    def on_batch_complete(self, category: ToolCategory, batch_size: int, elapsed_s: float) -> None:
        """Records a completed batch for later inclusion in a telemetry
        snapshot (Requirement 2.10). Appends the batch to both the overall
        and per-category batch lists, and increments the total
        `parallel_batches` counter.
        """
        record = BatchRecord(category=category, batch_size=batch_size, elapsed_s=elapsed_s)
        self._batches.append(record)
        self._batches_by_category[category].append(record)
        self._parallel_batches += 1

    def maximum_concurrency(self, category: Optional[ToolCategory] = None) -> int:
        """The maximum number of overlapping RUNNING tasks observed so far.

        If `category` is given, returns that category's own peak. If
        `category` is None, returns the overall peak -- the maximum, over
        all points in time, of the *sum* of every category's concurrently-
        running count at that instant (not the sum of each category's
        individual peak, since those peaks may have occurred at different
        times).
        """
        if category is None:
            return self._max_concurrency_overall
        return self._max_concurrency_per_category.get(category, 0)

    @property
    def parallel_batches(self) -> int:
        """Total count of batches recorded so far, across all categories."""
        return self._parallel_batches

    def batches(self, category: Optional[ToolCategory] = None) -> list[BatchRecord]:
        """Recorded batches so far, optionally filtered to a single category."""
        if category is None:
            return list(self._batches)
        return list(self._batches_by_category.get(category, []))

    def compute_critical_path_latency(self, tasks: list[ToolTask]) -> float:
        """Computes Critical_Path_Latency over `tasks` (Requirements 3.6, 4.8).

        Builds the dependency DAG implied by `tasks`' `depends_on` edges
        (an edge points FROM a dependent task TO the task(s) it depends
        on), computes each task's own measured duration as
        `completed_at - started_at` (0.0 if either timestamp is missing --
        e.g. a `SKIPPED`/`CANCELLED` task that never actually ran), and
        returns the maximum over all root-to-leaf paths in the DAG of the
        summed durations along that path.

        This is computed as `max(longest_path_ending_at(task) for task in
        tasks)`, where `longest_path_ending_at(task)` is `task`'s own
        duration plus the longest `longest_path_ending_at(dep)` among its
        known dependencies (0 if it has none, or none of its declared
        dependencies are present in `tasks`) -- every root-to-leaf path's
        endpoint is some task, and `longest_path_ending_at` for that task
        already accounts for the entire chain leading up to it, so this
        correctly captures "the longest dependency chain" rather than the
        sum of all task durations.

        `depends_on` entries that do not refer to a task present in
        `tasks` are ignored (this method is meant to work on an arbitrary
        subset/batch of tasks, not necessarily the full graph). Returns
        `0.0` for an empty `tasks` list.

        Raises `CriticalPathCycleDetectedError` if `tasks`' `depends_on`
        edges (restricted to ids present in `tasks`) contain a cycle --
        the Scheduler's own `DependencyGraph` already prevents cycles from
        ever being submitted (Task 3.2's `CycleDetectedError`), so this is
        a defensive guard against malformed/adversarial input rather than
        an expected runtime path.
        """
        if not tasks:
            return 0.0

        by_id: dict[str, ToolTask] = {task.task_id: task for task in tasks}
        memo: dict[str, float] = {}
        visiting: set[str] = set()

        def duration_of(task: ToolTask) -> float:
            if task.started_at is None or task.completed_at is None:
                return 0.0
            return task.completed_at - task.started_at

        def longest_path_ending_at(task_id: str) -> float:
            if task_id in memo:
                return memo[task_id]
            if task_id in visiting:
                raise CriticalPathCycleDetectedError(
                    "Cycle detected in depends_on graph while computing "
                    f"critical path latency (revisited task_id={task_id!r})"
                )

            visiting.add(task_id)
            task = by_id[task_id]
            best_upstream = 0.0
            for dep_id in task.depends_on:
                if dep_id not in by_id:
                    # Not part of this batch/subset -- ignore.
                    continue
                best_upstream = max(best_upstream, longest_path_ending_at(dep_id))
            visiting.discard(task_id)

            result = duration_of(task) + best_upstream
            memo[task_id] = result
            return result

        return max(longest_path_ending_at(task_id) for task_id in by_id)
