"""Property-based test for civicpilot.scheduler.scheduler.Scheduler (Task 4.5).

# Feature: civicpilot, Property 2: Concurrency never exceeds the configured maximum

Covers Property 2 from the design's Correctness Properties section: *for
all* points in time during a run and *for all* tool categories, the number
of tasks in that category with status `RUNNING` SHALL never exceed that
category's configured concurrency limit.

Unlike `tests/scheduler/test_concurrency_gate.py` (which proves the
guarantee holds at the `ConcurrencyGates` semaphore layer alone, in
isolation), this test drives the guarantee end to end through the full
`Scheduler`: `submit()` -> dependency resolution -> priority dispatch ->
concurrency-gated `_execute()`. This is the same "tracking fake executor"
pattern `TestConcurrencyEnforcement` in `tests/scheduler/test_scheduler.py`
uses for its single fixed example (5 tasks / limit 2); here Hypothesis
generates many (task_count, concurrency_limit) combinations, always with
task_count intentionally allowed to exceed concurrency_limit, to search for
any combination where the Scheduler's dispatch loop lets more tasks run
concurrently than the configured gate should allow.

Validates: Requirements 2.3, 3.7, 9.3, 11.2, 23.5
"""

from __future__ import annotations

import asyncio

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from civicpilot.scheduler.models import TaskStatus, ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler

#: All generated tasks in a single example share this category -- Property
#: 2 is scoped per category, so exercising one category at a time (with a
#: varying limit) is sufficient to search the property's input space; the
#: per-category isolation of the underlying gates is already covered by
#: tests/scheduler/test_concurrency_gate.py's TestCategoriesGatedIndependently.
_CATEGORY = ToolCategory.SEARCH

#: Deliberately tiny, fixed artificial duration for every generated task.
#: This is a timing/async property test (same caveat as Task 4.4's
#: independent-task-concurrency property test): real wall-clock sleeps are
#: involved, so examples cannot be made arbitrarily numerous without the
#: suite becoming slow. Keeping the duration this small (and max_examples
#: below at 25, not the general 100-minimum guidance) is a documented,
#: deliberate deviation to keep the suite fast while still exercising many
#: distinct (task_count, concurrency_limit) combinations.
_TASK_DURATION_S = 0.03


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


async def _run_property_case(task_count: int, concurrency_limit: int) -> int:
    """Submits `task_count` independent same-category tasks to a Scheduler
    configured with `concurrency_limit` for that category, runs it to
    completion, and returns the maximum number of tasks observed
    concurrently RUNNING at any point in time."""
    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def tracking_executor(task: ToolTask) -> str:
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
        await asyncio.sleep(_TASK_DURATION_S)
        async with lock:
            active -= 1
        return "ok"

    scheduler = Scheduler(
        concurrency_limits={_CATEGORY: concurrency_limit},
        executor=tracking_executor,
    )
    tasks = [_make_task(f"t{i}", priority=(i % 10) + 1) for i in range(task_count)]
    futures = [await scheduler.submit(task) for task in tasks]

    run_task = asyncio.create_task(scheduler.run())
    try:
        finished = await asyncio.wait_for(asyncio.gather(*futures), timeout=10.0)
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)

    assert all(task.status is TaskStatus.COMPLETED for task in finished), (
        "every submitted task should reach COMPLETED via the fake executor"
    )
    return max_active


@given(
    task_count=st.integers(min_value=3, max_value=10),
    concurrency_limit=st.integers(min_value=1, max_value=5),
)
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_concurrency_never_exceeds_configured_limit_end_to_end(
    task_count: int, concurrency_limit: int
) -> None:
    # Feature: civicpilot, Property 2: Concurrency never exceeds the
    # configured maximum
    max_observed = asyncio.run(_run_property_case(task_count, concurrency_limit))

    assert max_observed <= concurrency_limit, (
        f"observed {max_observed} concurrently RUNNING tasks in category "
        f"{_CATEGORY!r} but the configured limit was {concurrency_limit}"
    )
