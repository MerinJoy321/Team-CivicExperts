"""Property-based test for civicpilot.scheduler.scheduler.Scheduler (Task 5.5).

# Feature: civicpilot, Property 7: A sibling's failure never blocks or
# delays independent tasks

Covers Property 7 from the design's Correctness Properties section: for any
two tasks A and B with no dependency relationship between them, if A fails
(reaches FAILED or TIMED_OUT), B's eligibility, start time, and completion
SHALL be unaffected by A's failure -- B SHALL reach a terminal state as if
A had succeeded, modulo A's own outcome not being available to B.

Task 5.3's `TestFailureIsolation` in test_scheduler.py already covers this
exact property with a handful of hand-picked example scenarios (one FAILED
sibling, one TIMED_OUT sibling, and a fast-failure-doesn't-delay-a-slow-
success timing check). This test generalizes that coverage: it generates
N (2-6) independent (no depends_on), same-category ToolTasks where each is
randomly designated to either succeed or fail after a short duration
(constrained so at least one of each is present in every example, so the
isolation check is meaningfully exercised every time), runs them all
through a real Scheduler with concurrency generously above the task count
(so no task ever waits on a slot -- this test is purely about failure
isolation, not concurrency gating), and asserts every "succeed" task
resolves to COMPLETED and every "fail" task resolves to FAILED, with no
successful task's measured duration meaningfully exceeding its own intended
sleep duration (i.e. it was not delayed by a sibling's failure).

This is a real-time-based property test (real asyncio.sleep durations
involved, same as Tasks 4.4/4.5/4.6's property tests), so `max_examples` is
reduced from the design's "100 minimum" guidance for pure-logic properties,
and durations are kept small -- a documented, deliberate deviation
consistent with how Tasks 4.4/4.5/4.6 handled the identical tradeoff (see
test_scheduler_properties.py, test_scheduler_concurrency_properties.py, and
test_scheduler_priority_properties.py's module docstrings).

Validates: Requirements 2.7, 5.2, 8.7, 11.5, 19.1, 19.2
"""

from __future__ import annotations

import asyncio

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from civicpilot.scheduler.models import TaskStatus, ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler

# Real asyncio.sleep-based durations, kept small so each Hypothesis example
# stays fast while remaining comfortably above typical dispatch-loop/event
# loop scheduling overhead (observed up to ~15-20ms on Windows) so a
# meaningfully-delayed task would show up clearly against the tolerance
# used below.
_DURATION_MIN_S = 0.02
_DURATION_MAX_S = 0.05

# Reduced from the design's "100 minimum" for pure-logic properties: this
# property drives real `asyncio.sleep`-based execution end to end through
# the Scheduler, so each example costs real wall-clock time rather than
# being near-instantaneous pure computation. 25 examples still exercises a
# wide range of task counts, outcome assignments, and duration
# combinations while keeping the suite fast -- the same deliberate
# deviation Tasks 4.4/4.5/4.6 documented for their own timing-based
# property tests.
_MAX_EXAMPLES = 25

# Generous tolerance for scheduling overhead when checking that a
# successful task's own measured duration was not inflated by a sibling's
# failure. Expressed as an additive margin (seconds) on top of the task's
# own intended sleep duration, sized well above the small durations above
# and any single dispatch-loop poll interval.
_DURATION_TOLERANCE_S = 0.25


def _make_task(task_id: str, *, duration: float, should_fail: bool) -> ToolTask:
    return ToolTask(
        task_id=task_id,
        category=ToolCategory.SEARCH,
        tool_name="fake_tool",
        params={"duration": duration, "should_fail": should_fail},
        priority=5,
        timeout_ms=5000,
        depends_on=[],
        agent_role="Researcher_Agent",
    )


async def _mixed_executor(task: ToolTask) -> str:
    await asyncio.sleep(task.params["duration"])
    if task.params["should_fail"]:
        raise ValueError("boom")
    return "ok"


async def _run_mixed_outcome_tasks(
    outcomes: list[tuple[float, bool]]
) -> list[ToolTask]:
    """Submits one independent (no depends_on) ToolTask per
    (duration, should_fail) pair in `outcomes`, all in the same category
    with a concurrency limit generously above the task count (so none has
    to wait on a slot), runs the scheduler to completion, and returns the
    finished ToolTask objects in the same order as `outcomes`."""
    n = len(outcomes)
    limits = {category: max(n, 5) for category in ToolCategory}
    scheduler = Scheduler(concurrency_limits=limits, executor=_mixed_executor)

    tasks = [
        _make_task(f"t{i}", duration=duration, should_fail=should_fail)
        for i, (duration, should_fail) in enumerate(outcomes)
    ]
    futures = [await scheduler.submit(t) for t in tasks]

    run_task = asyncio.create_task(scheduler.run())
    try:
        finished = await asyncio.wait_for(asyncio.gather(*futures), timeout=5.0)
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=1.0)
    return finished


# Each element is (duration, should_fail). Generated as a list of 2-6
# entries; the property function below discards (via Hypothesis `assume`)
# any example where every entry shares the same should_fail value, since a
# meaningful isolation check requires at least one success and at least
# one failure present together.
_outcome_strategy = st.lists(
    st.tuples(
        st.floats(min_value=_DURATION_MIN_S, max_value=_DURATION_MAX_S),
        st.booleans(),
    ),
    min_size=2,
    max_size=6,
)


@given(outcomes=_outcome_strategy)
@settings(max_examples=_MAX_EXAMPLES, deadline=None)
def test_sibling_failure_never_blocks_or_delays_independent_successes(
    outcomes: list[tuple[float, bool]]
) -> None:
    # Feature: civicpilot, Property 7: A sibling's failure never blocks or
    # delays independent tasks
    should_fail_flags = [should_fail for _, should_fail in outcomes]
    # Only meaningful when at least one task succeeds and at least one
    # fails -- an all-succeed or all-fail example doesn't exercise
    # isolation between differing outcomes at all.
    assume(any(should_fail_flags) and not all(should_fail_flags))

    finished = asyncio.run(_run_mixed_outcome_tasks(outcomes))

    for task, (duration, should_fail) in zip(finished, outcomes):
        assert task.started_at is not None
        assert task.completed_at is not None

        if should_fail:
            assert task.status is TaskStatus.FAILED, (
                f"{task.task_id} was designated to fail but resolved to "
                f"{task.status!r}"
            )
        else:
            assert task.status is TaskStatus.COMPLETED, (
                f"{task.task_id} was designated to succeed but resolved to "
                f"{task.status!r} -- a sibling's failure must never affect "
                "an independent task's own terminal status"
            )
            measured_duration = task.completed_at - task.started_at
            assert measured_duration < duration + _DURATION_TOLERANCE_S, (
                f"{task.task_id}'s measured duration {measured_duration:.4f}s "
                f"exceeds its own intended duration {duration:.4f}s plus "
                "tolerance -- it appears to have been delayed by a "
                "sibling's failure"
            )
