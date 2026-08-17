"""Unit tests for civicpilot.scheduler.priority_queue (Task 4.1).

Covers dispatch ordering by priority (lower number first), tie-break by
`dependency_satisfied_at` (earlier first) when priorities are equal, and
the insertion-order tie-break when both priority and
`dependency_satisfied_at` are equal.
"""

from __future__ import annotations

import pytest

from civicpilot.scheduler.models import ToolCategory, ToolTask
from civicpilot.scheduler.priority_queue import (
    CategoryPriorityQueue,
    PriorityQueueEmptyError,
)


def _make_task(task_id: str, priority: int, dependency_satisfied_at: float | None, **overrides) -> ToolTask:
    defaults = dict(
        task_id=task_id,
        category=ToolCategory.SEARCH,
        tool_name="tavily_search",
        params={"query": "pm kisan eligibility"},
        priority=priority,
        timeout_ms=6000,
        depends_on=[],
        agent_role="Researcher_Agent",
        dependency_satisfied_at=dependency_satisfied_at,
    )
    defaults.update(overrides)
    return ToolTask(**defaults)


# ---------------------------------------------------------------------------
# Priority ordering: lower priority number dispatched first.
# ---------------------------------------------------------------------------


def test_pop_dispatches_lower_priority_number_first():
    queue = CategoryPriorityQueue(ToolCategory.SEARCH)
    low_priority = _make_task("low", priority=8, dependency_satisfied_at=1.0)
    high_priority = _make_task("high", priority=2, dependency_satisfied_at=5.0)

    # Push in an order where naive FIFO would get this wrong.
    queue.push(low_priority)
    queue.push(high_priority)

    assert queue.pop() is high_priority
    assert queue.pop() is low_priority


def test_pop_dispatches_in_strict_priority_order_across_many_tasks():
    queue = CategoryPriorityQueue(ToolCategory.SEARCH)
    tasks = [
        _make_task(f"t{p}", priority=p, dependency_satisfied_at=float(p))
        for p in [5, 1, 10, 3, 7]
    ]
    for task in tasks:
        queue.push(task)

    dispatched_priorities = [queue.pop().priority for _ in range(len(tasks))]

    assert dispatched_priorities == sorted(dispatched_priorities)
    assert dispatched_priorities == [1, 3, 5, 7, 10]


# ---------------------------------------------------------------------------
# Tie-break by dependency_satisfied_at when priorities are equal.
# ---------------------------------------------------------------------------


def test_equal_priority_ties_broken_by_earlier_dependency_satisfied_at():
    queue = CategoryPriorityQueue(ToolCategory.SEARCH)
    later = _make_task("later", priority=3, dependency_satisfied_at=10.0)
    earlier = _make_task("earlier", priority=3, dependency_satisfied_at=2.0)

    # Push the later-ready task first to prove ordering isn't just FIFO.
    queue.push(later)
    queue.push(earlier)

    assert queue.pop() is earlier
    assert queue.pop() is later


def test_none_dependency_satisfied_at_is_treated_as_ready_immediately():
    queue = CategoryPriorityQueue(ToolCategory.SEARCH)
    with_timestamp = _make_task("with_ts", priority=4, dependency_satisfied_at=0.001)
    without_timestamp = _make_task("without_ts", priority=4, dependency_satisfied_at=None)

    queue.push(with_timestamp)
    queue.push(without_timestamp)

    # None is treated as "ready immediately" (earliest possible), so it
    # is dispatched before a task with a real, later timestamp.
    assert queue.pop() is without_timestamp
    assert queue.pop() is with_timestamp


# ---------------------------------------------------------------------------
# Third-level tie-break: insertion order, when priority AND
# dependency_satisfied_at are both equal.
# ---------------------------------------------------------------------------


def test_equal_priority_and_dependency_satisfied_at_tiebreaks_by_insertion_order():
    queue = CategoryPriorityQueue(ToolCategory.SEARCH)
    first_in = _make_task("first_in", priority=5, dependency_satisfied_at=1.0)
    second_in = _make_task("second_in", priority=5, dependency_satisfied_at=1.0)
    third_in = _make_task("third_in", priority=5, dependency_satisfied_at=1.0)

    queue.push(first_in)
    queue.push(second_in)
    queue.push(third_in)

    assert queue.pop() is first_in
    assert queue.pop() is second_in
    assert queue.pop() is third_in


# ---------------------------------------------------------------------------
# Empty-queue behavior.
# ---------------------------------------------------------------------------


def test_pop_on_empty_queue_raises():
    queue = CategoryPriorityQueue(ToolCategory.SEARCH)

    with pytest.raises(PriorityQueueEmptyError):
        queue.pop()


def test_peek_on_empty_queue_raises():
    queue = CategoryPriorityQueue(ToolCategory.SEARCH)

    with pytest.raises(PriorityQueueEmptyError):
        queue.peek()


def test_peek_does_not_remove_task():
    queue = CategoryPriorityQueue(ToolCategory.SEARCH)
    task = _make_task("only", priority=1, dependency_satisfied_at=1.0)
    queue.push(task)

    assert queue.peek() is task
    assert len(queue) == 1
    assert queue.pop() is task


def test_len_and_is_empty_reflect_queue_state():
    queue = CategoryPriorityQueue(ToolCategory.SEARCH)
    assert len(queue) == 0
    assert queue.is_empty()

    queue.push(_make_task("a", priority=1, dependency_satisfied_at=1.0))
    assert len(queue) == 1
    assert not queue.is_empty()

    queue.pop()
    assert len(queue) == 0
    assert queue.is_empty()
