"""Property-based test for civicpilot.scheduler.scheduler.Scheduler (Task 4.6).

# Feature: civicpilot, Property 4: Priority and FIFO tie-break ordering is respected

Covers Property 4 from the design's Correctness Properties section: for any
set of eligible tasks competing for the same category's concurrency slots,
the Scheduler SHALL dispatch the task with the numerically lowest `priority`
value first, and among tasks of equal priority SHALL dispatch in ascending
order of `dependency_satisfied_at`.

`tests/scheduler/test_priority_queue.py` already thoroughly covers this
exact ordering rule at the `CategoryPriorityQueue` layer in isolation (push
tasks with pre-set priority/dependency_satisfied_at values directly, pop,
and assert order). This test instead proves the same guarantee holds once
dispatch is constrained by real concurrency -- through the full `Scheduler`
(`submit()` -> dependency resolution -> priority dispatch ->
concurrency-gated `_execute()`, Task 4.3) rather than the priority queue in
isolation -- by submitting more independent, same-category tasks than the
category has concurrency slots for (exactly one slot, so dispatch is fully
serialized and the start order is unambiguous and directly observable) and
recording the actual order tasks start executing.

`ToolTask.dependency_satisfied_at` cannot be pre-set through `submit()`:
`DependencyGraph.resolve()` unconditionally overwrites it with `now_s()`
for every no-dependency `PENDING` task the instant it becomes `ELIGIBLE`
(see `civicpilot/scheduler/dependency_graph.py`), regardless of any value
the task was constructed with. Submitting a task pre-set to `ELIGIBLE`
also does not work: `resolve()` only reports (and `_resolve_dependencies`
only pushes into a category's priority queue) tasks whose status *changed*
during that call, so an already-`ELIGIBLE` task submitted directly would
never be pushed into its category's queue at all. So this test uses
approach (a) from the task description: all tasks are submitted (in a
controlled order) while still `PENDING`, and the *actual*
`dependency_satisfied_at` values the Scheduler assigns are read back from
the finished tasks to compute the expected dispatch order, rather than
assuming a value. In practice, every independent task submitted before
`run()` starts is resolved to `ELIGIBLE` in the very same first
`DependencyGraph.resolve()` pass, so their `dependency_satisfied_at` values
tie (or nearly tie, subject to clock resolution); `sorted(...,
key=(priority, dependency_satisfied_at))` is a stable sort, so any such tie
naturally preserves submission order as the final tie-break -- exactly the
insertion-order fallback `CategoryPriorityQueue.push` documents as its own
third-level tie-break.

This is a real-time-based property test (same caveat as Tasks 4.4/4.5's
property tests: it drives the actual `asyncio` event loop through a fake
`asyncio.sleep`-based executor), so `max_examples` and task durations are
kept small -- a documented, deliberate deviation from the design's "100
minimum" guidance for pure-logic properties, consistent with how Tasks 4.4
(`test_scheduler_properties.py`) and 4.5
(`test_scheduler_concurrency_properties.py`) handle the same real-time
testing tradeoff.

Validates: Requirement 2.4
"""

from __future__ import annotations

import asyncio

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from civicpilot.scheduler.models import TaskStatus, ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler

#: All generated tasks in a single example share this category, with a
#: concurrency limit of exactly 1 (see _run_property_case), so dispatch is
#: fully serialized and the start order is unambiguous and directly
#: observable.
_CATEGORY = ToolCategory.FETCH

#: Deliberately tiny, fixed artificial duration for every generated task --
#: see module docstring's real-time-testing-tradeoff note.
_TASK_DURATION_S = 0.01

#: Reduced from the design's "100 minimum" for pure-logic properties, for
#: the same reason Tasks 4.4/4.5 reduce theirs: each example drives real
#: asyncio.sleep-based execution rather than being near-instantaneous pure
#: computation. 25 examples still exercises a wide range of task counts and
#: priority assignments (including many equal-priority ties) while keeping
#: the suite fast.
_MAX_EXAMPLES = 25


def _make_task(task_id: str, priority: int) -> ToolTask:
    return ToolTask(
        task_id=task_id,
        category=_CATEGORY,
        tool_name="fake_tool",
        params={},
        priority=priority,
        timeout_ms=5000,
        depends_on=[],
        agent_role="Researcher_Agent",
    )


async def _run_property_case(priorities: list[int]) -> tuple[list[str], list[ToolTask]]:
    """Submits one independent, no-dependency ToolTask per priority in
    `priorities` (in list order), all sharing `_CATEGORY` with a
    concurrency limit of exactly 1, runs the Scheduler to completion, and
    returns (start_order, tasks):

    - start_order: task_ids in the actual order they began executing,
      recorded by a tracking fake executor appending under a lock.
    - tasks: the submitted ToolTask objects (carrying the
      Scheduler-assigned dependency_satisfied_at after completion), in
      the same order they were submitted.
    """
    start_order: list[str] = []
    lock = asyncio.Lock()

    async def tracking_executor(task: ToolTask) -> str:
        async with lock:
            start_order.append(task.task_id)
        await asyncio.sleep(_TASK_DURATION_S)
        return "ok"

    scheduler = Scheduler(
        concurrency_limits={_CATEGORY: 1},
        executor=tracking_executor,
    )

    tasks = [_make_task(f"t{i}", priority=p) for i, p in enumerate(priorities)]
    # All tasks are submitted (while still PENDING) before run() starts, so
    # every task becomes ELIGIBLE in the same first DependencyGraph.resolve()
    # pass -- see module docstring.
    futures = [await scheduler.submit(t) for t in tasks]

    run_task = asyncio.create_task(scheduler.run())
    try:
        finished = await asyncio.wait_for(asyncio.gather(*futures), timeout=10.0)
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    assert all(task.status is TaskStatus.COMPLETED for task in finished), (
        "every submitted task should reach COMPLETED via the fake executor"
    )
    return start_order, tasks


@given(
    priorities=st.lists(st.integers(min_value=1, max_value=10), min_size=2, max_size=8),
)
@settings(
    max_examples=_MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_dispatch_order_matches_priority_then_dependency_satisfied_at(
    priorities: list[int],
) -> None:
    # Feature: civicpilot, Property 4: Priority and FIFO tie-break ordering
    # is respected
    start_order, tasks = asyncio.run(_run_property_case(priorities))

    for task in tasks:
        assert task.dependency_satisfied_at is not None

    # Expected order: ascending priority, ties broken by ascending
    # dependency_satisfied_at -- the actual Scheduler-assigned values, read
    # back from the finished tasks rather than assumed. sorted() is a
    # stable sort, so any remaining tie in dependency_satisfied_at (the
    # common case here -- see module docstring) preserves the original
    # submission order as the final tie-break, matching
    # CategoryPriorityQueue's own insertion-order fallback.
    expected_order = [
        task.task_id
        for task in sorted(tasks, key=lambda t: (t.priority, t.dependency_satisfied_at))
    ]

    assert start_order == expected_order, (
        f"expected dispatch order {expected_order} (by priority, then "
        f"dependency_satisfied_at) but observed {start_order}"
    )
