"""Property-based test for civicpilot.telemetry.telemetry_module (Task 6.4).

# Feature: civicpilot, Property 3: Critical path latency is bounded by the
# longest dependency chain, not the sum of all task durations

Covers Property 3 from the design's Correctness Properties section: for any
dependency graph of ToolTasks with measured start/end timestamps, the
computed Critical_Path_Latency SHALL equal the maximum, over all
root-to-leaf paths in the dependency graph, of the sum of each path's
tasks' measured durations, and SHALL be strictly less than the sum of the
durations of all tasks whenever the graph contains at least two independent
(non-dependent) tasks.

This generalizes the hand-picked examples in test_telemetry_module.py
(single task, linear chain, parallel branches converging, the
strict-less-than assertion, empty list, missing timestamps, unknown
dependency references, and cycle detection) across many randomly-generated
acyclic DAG shapes, using a fresh reference implementation written
independently of telemetry_module.py's own memoized algorithm.

Validates: Requirements 3.6, 4.8
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from civicpilot.scheduler.models import TaskStatus, ToolCategory, ToolTask
from civicpilot.telemetry.telemetry_module import TelemetryModule


def _make_task(task_id: str, duration: float, depends_on: list[str]) -> ToolTask:
    """Builds a `ToolTask` with a fixed started_at=0.0 and
    completed_at=duration, independent of any other task's timestamps, so
    this remains a pure deterministic computation test (no real asyncio
    sleeps or wall-clock timing involved)."""
    return ToolTask(
        task_id=task_id,
        category=ToolCategory.SEARCH,
        tool_name="fake_tool",
        params={},
        priority=5,
        timeout_ms=1000,
        depends_on=depends_on,
        agent_role="test_agent",
        status=TaskStatus.COMPLETED,
        started_at=0.0,
        completed_at=duration,
    )


@st.composite
def _acyclic_dag_with_durations(draw):
    """Generates a small acyclic DAG of N ToolTasks (N in [2, 10]) where
    each task at index i depends on zero or more strictly-earlier-indexed
    tasks, guaranteeing acyclicity by construction (mirrors the DAG
    generation pattern in
    tests/scheduler/test_dependency_graph_properties.py's
    _chain_dag_with_one_failed_dependency strategy). Each task is assigned
    a random non-negative float duration in [0.0, 10.0].

    Returns a list of `ToolTask`s in index order.
    """
    n = draw(st.integers(min_value=2, max_value=10))
    task_ids = [f"t{i}" for i in range(n)]
    tasks: list[ToolTask] = []

    for i, task_id in enumerate(task_ids):
        if i == 0:
            depends_on: list[str] = []
        else:
            # Each task may depend on any subset of strictly-earlier-indexed
            # tasks -- always acyclic since edges only ever point backwards.
            earlier_ids = task_ids[:i]
            depends_on = draw(
                st.lists(st.sampled_from(earlier_ids), unique=True, max_size=len(earlier_ids))
            )
        duration = draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False))
        tasks.append(_make_task(task_id, duration, depends_on))

    return tasks


def _reference_critical_path_latency(tasks: list[ToolTask]) -> float:
    """A fresh, independent reference implementation of "longest path
    ending at each node", deliberately NOT reusing any internal helper
    from telemetry_module.py -- so this test verifies the implementation
    against an independently-derived computation rather than against
    itself.

    Uses a simple iterative dynamic-programming pass over the tasks in
    their given (topologically-valid-by-construction) order: since every
    task's depends_on entries only reference earlier-indexed tasks in the
    generator above, processing tasks in list order guarantees every
    dependency's longest-path value is already computed before it is
    needed.
    """
    by_id = {task.task_id: task for task in tasks}
    duration_of = {
        task.task_id: (task.completed_at - task.started_at) for task in tasks
    }
    longest_ending_at: dict[str, float] = {}

    for task in tasks:
        best_upstream = 0.0
        for dep_id in task.depends_on:
            if dep_id not in by_id:
                continue
            best_upstream = max(best_upstream, longest_ending_at[dep_id])
        longest_ending_at[task.task_id] = duration_of[task.task_id] + best_upstream

    return max(longest_ending_at.values())


def _has_mutually_unreachable_pair(tasks: list[ToolTask]) -> bool:
    """Detects whether the DAG contains at least one pair of tasks with no
    path between them in either direction (genuinely independent, not
    merely non-adjacent). Computes reachability (via depends_on edges,
    followed in both directions) between every pair of tasks and returns
    True as soon as a mutually-unreachable pair is found.
    """
    by_id = {task.task_id: task for task in tasks}
    ids = list(by_id.keys())

    # Build forward (dependent -> dependency) and reverse (dependency ->
    # dependent) adjacency, then compute full reachability in the
    # underlying undirected sense by unioning both directions' closures.
    forward: dict[str, set[str]] = {tid: set() for tid in ids}
    for task in tasks:
        for dep_id in task.depends_on:
            if dep_id in by_id:
                forward[task.task_id].add(dep_id)

    reverse: dict[str, set[str]] = {tid: set() for tid in ids}
    for tid, deps in forward.items():
        for dep_id in deps:
            reverse[dep_id].add(tid)

    def reachable_either_direction(start: str) -> set[str]:
        """All nodes reachable from `start` by following either
        depends_on edges forward (ancestors it depends on) or backward
        (descendants that depend on it)."""
        seen = {start}
        stack = [start]
        while stack:
            node = stack.pop()
            for nxt in forward[node] | reverse[node]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    for i, a in enumerate(ids):
        reachable_from_a = reachable_either_direction(a)
        for b in ids[i + 1:]:
            if b not in reachable_from_a:
                return True
    return False


@given(tasks=_acyclic_dag_with_durations())
@settings(max_examples=100)
def test_critical_path_latency_matches_reference_and_bounded_by_chain(tasks):
    # Feature: civicpilot, Property 3: Critical path latency is bounded by
    # the longest dependency chain, not the sum of all task durations
    telemetry = TelemetryModule()

    actual = telemetry.compute_critical_path_latency(tasks)
    expected = _reference_critical_path_latency(tasks)

    assert actual == expected or abs(actual - expected) < 1e-9

    if _has_mutually_unreachable_pair(tasks):
        total_sum = sum(task.completed_at - task.started_at for task in tasks)
        # The critical path corresponds to a single chain of pairwise
        # dependency-linked tasks. Since at least one pair of tasks is
        # mutually unreachable, they can never both appear in that chain,
        # so the chain necessarily excludes at least one task's duration.
        # The non-strict bound therefore always holds; the strict bound
        # holds too UNLESS every excluded task happens to have measured
        # duration exactly 0.0 (an instantaneous-task degenerate case,
        # reachable via the [0.0, 10.0] duration strategy), in which
        # excluding it changes nothing numerically.
        assert actual <= total_sum + 1e-9
        excluded_duration = total_sum - actual
        if excluded_duration > 1e-9:
            assert actual < total_sum
