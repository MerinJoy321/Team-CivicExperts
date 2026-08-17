"""Property-based test for civicpilot.scheduler.scheduler.Scheduler (Task 4.4).

# Feature: civicpilot, Property 1: Independent tasks never serialize

Covers Property 1 from the design's Correctness Properties section: for any
set of `ToolTask`s with no data dependency between them (empty `depends_on`)
and available concurrency slots in their category, the Scheduler SHALL
start all of them without any one task's start waiting on another's
completion; their execution windows SHALL overlap.

This is a real-time-based property test (it drives the actual `asyncio`
event loop through a fake `asyncio.sleep`-based executor and reads back the
Scheduler-assigned `started_at`/`completed_at` wall-clock timestamps,
per the design's `now_s()` monotonic-clock timing utility from Task 1.4),
unlike the pure-logic properties elsewhere in this suite (e.g. Property 5 in
test_dependency_graph_properties.py). Each Hypothesis example therefore
costs real wall-clock time (tens of milliseconds), so `max_examples` is
reduced from the design's "100 minimum" default and durations are kept at
the minimum that still reliably demonstrates concurrent execution -- this
is the documented, acceptable deviation the design's testing-strategy notes
anticipate for timing-based properties.

Validates: Requirements 1.3, 2.2, 5.1
"""

from __future__ import annotations

import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from civicpilot.scheduler.models import TaskStatus, ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler

# Kept small so real-time examples stay fast, while remaining large enough
# relative to asyncio/dispatch-loop scheduling overhead (observed up to
# ~15-20ms on Windows' default event loop) to reliably show overlapping
# windows -- and a comfortably-below-serial coarse elapsed-time margin --
# rather than measurement noise dominating the signal.
_DURATION_MIN_S = 0.15
_DURATION_MAX_S = 0.25

# Reduced from the design's "100 minimum" for pure-logic properties: this
# property drives real `asyncio.sleep`-based execution, so each example
# costs real wall-clock time (~1 dispatch-loop poll interval plus the
# task's own sleep duration) rather than being near-instantaneous pure
# computation. 40 examples still exercises a wide range of task counts and
# durations while keeping the suite fast; see module docstring.
_MAX_EXAMPLES = 40


def _make_independent_task(task_id: str, duration: float) -> ToolTask:
    return ToolTask(
        task_id=task_id,
        category=ToolCategory.SEARCH,
        tool_name="fake_tool",
        params={"duration": duration},
        priority=5,
        timeout_ms=5000,
        depends_on=[],
        agent_role="Researcher_Agent",
    )


async def _sleep_executor(task: ToolTask) -> str:
    await asyncio.sleep(task.params["duration"])
    return "ok"


async def _run_independent_tasks(durations: list[float]) -> list[ToolTask]:
    """Submits one independent (no depends_on) ToolTask per duration in
    `durations`, all in the same category with a concurrency limit
    generously above the task count (so none has to wait on a slot), runs
    the scheduler to completion, and returns the finished ToolTask objects
    (carrying Scheduler-assigned started_at/completed_at)."""
    n = len(durations)
    limits = {category: max(n, 5) for category in ToolCategory}
    scheduler = Scheduler(concurrency_limits=limits, executor=_sleep_executor)

    tasks = [_make_independent_task(f"t{i}", d) for i, d in enumerate(durations)]
    futures = [await scheduler.submit(t) for t in tasks]

    run_task = asyncio.create_task(scheduler.run())
    try:
        finished = await asyncio.wait_for(asyncio.gather(*futures), timeout=5.0)
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=1.0)
    return finished


def _windows_overlap(a: ToolTask, b: ToolTask) -> bool:
    """True iff [a.started_at, a.completed_at] intersects
    [b.started_at, b.completed_at]."""
    return a.started_at < b.completed_at and b.started_at < a.completed_at


@given(
    durations=st.lists(
        st.floats(min_value=_DURATION_MIN_S, max_value=_DURATION_MAX_S),
        min_size=2,
        max_size=5,
    )
)
@settings(max_examples=_MAX_EXAMPLES, deadline=None)
def test_independent_tasks_execution_windows_overlap(durations: list[float]) -> None:
    # Feature: civicpilot, Property 1: Independent tasks never serialize
    finished = asyncio.run(_run_independent_tasks(durations))

    assert all(t.status is TaskStatus.COMPLETED for t in finished)
    for t in finished:
        assert t.started_at is not None
        assert t.completed_at is not None

    # Full rigor: every task is independent, shares one category, and the
    # concurrency limit generously exceeds the task count, so *every* pair
    # of tasks must have overlapping execution windows -- none of them
    # should have had to wait for another to finish before starting.
    for i in range(len(finished)):
        for j in range(i + 1, len(finished)):
            a, b = finished[i], finished[j]
            assert _windows_overlap(a, b), (
                f"expected {a.task_id} [{a.started_at}, {a.completed_at}] to "
                f"overlap {b.task_id} [{b.started_at}, {b.completed_at}], "
                "but they ran serially"
            )

    # Corroborating, coarser-grained check: total measured wall-clock
    # elapsed time across all tasks is well under the sum of their
    # individual durations, which would only be true if they ran
    # concurrently rather than one after another.
    elapsed = max(t.completed_at for t in finished) - min(t.started_at for t in finished)
    total_duration = sum(durations)
    assert elapsed < total_duration * 0.85
