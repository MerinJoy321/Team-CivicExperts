"""Unit tests for civicpilot.scheduler.dependency_graph (Task 3.2).

Covers immediate eligibility for no-dependency tasks, eligibility
triggered by dependency completion (single and all-required), skip
propagation for each of FAILED/TIMED_OUT/CANCELLED individually,
multi-level transitive skip propagation, and cycle detection.
"""

from __future__ import annotations

import pytest

from civicpilot.scheduler import CycleDetectedError, DependencyGraph, TaskStatus, ToolCategory, ToolTask


def _make_task(task_id: str, depends_on: list[str] | None = None, **overrides) -> ToolTask:
    defaults = dict(
        task_id=task_id,
        category=ToolCategory.SEARCH,
        tool_name="tavily_search",
        params={"query": "pm kisan eligibility"},
        priority=1,
        timeout_ms=6000,
        depends_on=depends_on or [],
        agent_role="Researcher_Agent",
    )
    defaults.update(overrides)
    return ToolTask(**defaults)


# ---------------------------------------------------------------------------
# Base case: no dependencies -> immediately ELIGIBLE.
# ---------------------------------------------------------------------------


def test_task_with_no_dependencies_is_immediately_eligible():
    graph = DependencyGraph()
    task = _make_task("a")
    graph.add_task(task)

    changed = graph.resolve()

    assert task.status is TaskStatus.ELIGIBLE
    assert task.dependency_satisfied_at is not None
    assert changed == [task]


# ---------------------------------------------------------------------------
# Eligibility triggered by dependency completion.
# ---------------------------------------------------------------------------


def test_eligibility_triggered_by_single_dependency_completion():
    graph = DependencyGraph()
    a = _make_task("a", status=TaskStatus.COMPLETED)
    b = _make_task("b", depends_on=["a"])
    graph.add_tasks([a, b])

    graph.resolve()

    assert b.status is TaskStatus.ELIGIBLE
    assert b.dependency_satisfied_at is not None


def test_eligibility_requires_all_dependencies_not_just_one():
    graph = DependencyGraph()
    a = _make_task("a", status=TaskStatus.COMPLETED)
    b = _make_task("b", status=TaskStatus.RUNNING)
    c = _make_task("c", depends_on=["a", "b"])
    graph.add_tasks([a, b, c])

    graph.resolve()

    assert c.status is TaskStatus.PENDING

    b.status = TaskStatus.COMPLETED
    graph.resolve()

    assert c.status is TaskStatus.ELIGIBLE


def test_task_pending_while_dependency_still_running():
    graph = DependencyGraph()
    a = _make_task("a", status=TaskStatus.RUNNING)
    b = _make_task("b", depends_on=["a"])
    graph.add_tasks([a, b])

    graph.resolve()

    assert b.status is TaskStatus.PENDING


# ---------------------------------------------------------------------------
# Skip propagation for each SKIP_PROPAGATING_STATUSES member individually.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dep_status",
    [TaskStatus.FAILED, TaskStatus.TIMED_OUT, TaskStatus.CANCELLED],
)
def test_dependent_is_skipped_when_dependency_reaches_propagating_status(dep_status):
    graph = DependencyGraph()
    a = _make_task("a", status=dep_status)
    b = _make_task("b", depends_on=["a"])
    graph.add_tasks([a, b])

    graph.resolve()

    assert b.status is TaskStatus.SKIPPED


def test_dependent_never_transitions_through_running_when_skipped():
    """A skipped task must never execute -- its status goes straight from
    PENDING to SKIPPED, never touching ELIGIBLE or RUNNING."""
    graph = DependencyGraph()
    a = _make_task("a", status=TaskStatus.FAILED)
    b = _make_task("b", depends_on=["a"])
    graph.add_tasks([a, b])

    graph.resolve()

    assert b.status is TaskStatus.SKIPPED
    assert b.status is not TaskStatus.ELIGIBLE
    assert b.status is not TaskStatus.RUNNING


# ---------------------------------------------------------------------------
# Multi-level transitive skip propagation.
# ---------------------------------------------------------------------------


def test_multi_level_transitive_skip_propagation():
    """A fails -> B (depends on A) is skipped -> C (depends only on B,
    never directly on A) is also skipped, all within a single resolve()
    call."""
    graph = DependencyGraph()
    a = _make_task("a", status=TaskStatus.FAILED)
    b = _make_task("b", depends_on=["a"])
    c = _make_task("c", depends_on=["b"])
    graph.add_tasks([a, b, c])

    graph.resolve()

    assert b.status is TaskStatus.SKIPPED
    assert c.status is TaskStatus.SKIPPED


def test_transitive_skip_propagation_does_not_affect_independent_siblings():
    graph = DependencyGraph()
    a = _make_task("a", status=TaskStatus.FAILED)
    b = _make_task("b", depends_on=["a"])
    independent = _make_task("independent")
    graph.add_tasks([a, b, independent])

    graph.resolve()

    assert b.status is TaskStatus.SKIPPED
    assert independent.status is TaskStatus.ELIGIBLE


# ---------------------------------------------------------------------------
# Cycle detection.
# ---------------------------------------------------------------------------


def test_direct_cycle_is_detected():
    graph = DependencyGraph()
    a = _make_task("a", depends_on=["b"])
    b = _make_task("b", depends_on=["a"])

    with pytest.raises(CycleDetectedError):
        graph.add_tasks([a, b])


def test_self_dependency_is_detected_as_cycle():
    graph = DependencyGraph()
    a = _make_task("a", depends_on=["a"])

    with pytest.raises(CycleDetectedError):
        graph.add_task(a)


def test_transitive_cycle_is_detected():
    graph = DependencyGraph()
    a = _make_task("a", depends_on=["c"])
    b = _make_task("b", depends_on=["a"])
    c = _make_task("c", depends_on=["b"])

    with pytest.raises(CycleDetectedError):
        graph.add_tasks([a, b, c])


def test_acyclic_graph_does_not_raise():
    graph = DependencyGraph()
    a = _make_task("a")
    b = _make_task("b", depends_on=["a"])
    c = _make_task("c", depends_on=["a", "b"])

    graph.add_tasks([a, b, c])  # should not raise
