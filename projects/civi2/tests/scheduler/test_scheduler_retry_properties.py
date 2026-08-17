"""Property-based test for the Scheduler's retry cap (Task 5.4).

# Feature: civicpilot, Property 6: Retry count never exceeds one

Covers Property 6 from the design's Correctness Properties section: for any
ToolTask execution, retries_used SHALL never exceed 1, regardless of how
many times the underlying call fails, and a non-recoverable failure SHALL
result in retries_used == 0.

This generalizes the example-based coverage in
tests/scheduler/test_scheduler.py's TestRetryPolicy class across many
random failure-pattern combinations using Hypothesis, running a real
Scheduler end to end (submit()/run()) with a fake executor driven by the
generated pattern -- no real Tavily/Jina/etc. clients, no artificial
sleeps (the executor resolves near-instantly), consistent with Task 5.2's
retry policy: on RecoverableToolError, retry exactly once using the same
timeout as the original attempt; any other exception on the first attempt
is non-retryable and goes straight to FAILED with no retry.

Validates: Requirements 2.6, 18.7, 18.8
"""

from __future__ import annotations

import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from civicpilot.scheduler.errors import RecoverableToolError
from civicpilot.scheduler.models import TaskStatus, ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler

#: The three possible outcomes for a single executor attempt. Only two
#: attempts are ever possible for any ToolTask (one original attempt, plus
#: at most one retry per Requirement 2.6), so a pattern of exactly two
#: outcomes is sufficient to cover every reachable retry-policy path --
#: the second outcome is simply never consulted when the first attempt is
#: "succeed" or "non_recoverable".
_OUTCOME = st.sampled_from(["succeed", "recoverable", "non_recoverable"])

#: A failure pattern: (attempt_1_outcome, attempt_2_outcome).
_failure_patterns = st.tuples(_OUTCOME, _OUTCOME)


def _make_task(pattern: tuple[str, str]) -> ToolTask:
    return ToolTask(
        task_id="t1",
        category=ToolCategory.SEARCH,
        tool_name="fake_tool",
        params={"pattern": pattern},
        priority=5,
        timeout_ms=6000,
        depends_on=[],
        agent_role="Researcher_Agent",
    )


def _make_pattern_executor(attempt_counter: list[int]):
    """Builds an executor that consults the task's own pattern param and
    counts how many times it was actually invoked, so a test can verify a
    non-recoverable first attempt never triggers a second invocation."""

    async def executor(task: ToolTask) -> str:
        attempt_counter[0] += 1
        outcome = task.params["pattern"][attempt_counter[0] - 1]
        if outcome == "succeed":
            return "ok"
        if outcome == "recoverable":
            raise RecoverableToolError("transient failure")
        raise ValueError("non-recoverable failure")

    return executor


async def _run_pattern(pattern: tuple[str, str]) -> tuple[ToolTask, int]:
    """Submits a single task whose executor follows `pattern`, runs the
    Scheduler to completion, and returns the finished task plus how many
    times the executor was actually invoked."""
    attempt_counter = [0]
    executor = _make_pattern_executor(attempt_counter)
    scheduler = Scheduler(concurrency_limits={ToolCategory.SEARCH: 1}, executor=executor)
    task = _make_task(pattern)

    future = await scheduler.submit(task)
    run_task = asyncio.create_task(scheduler.run())
    try:
        finished = await asyncio.wait_for(future, timeout=2.0)
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=1.0)

    return finished, attempt_counter[0]


@given(pattern=_failure_patterns)
@settings(max_examples=100)
def test_retry_count_never_exceeds_one(pattern: tuple[str, str]) -> None:
    # Feature: civicpilot, Property 6: Retry count never exceeds one
    attempt1, attempt2 = pattern

    finished, attempts = asyncio.run(_run_pattern(pattern))

    # Universal safety bound: retries_used is always 0 or 1, never more.
    assert finished.retries_used in (0, 1)

    if attempt1 == "succeed":
        assert attempts == 1
        assert finished.retries_used == 0
        assert finished.status is TaskStatus.COMPLETED
        assert finished.result == "ok"

    elif attempt1 == "non_recoverable":
        # Non-recoverable failure on the first attempt: no retry at all,
        # the executor must never be invoked a second time.
        assert attempts == 1
        assert finished.retries_used == 0
        assert finished.status is TaskStatus.FAILED

    else:  # attempt1 == "recoverable"
        # Exactly one retry (attempt 2) occurs regardless of what attempt
        # 2's outcome is, and retries_used == 1.
        assert attempts == 2
        assert finished.retries_used == 1

        if attempt2 == "succeed":
            assert finished.status is TaskStatus.COMPLETED
            assert finished.result == "ok"
        else:
            # Whether attempt 2 raises RecoverableToolError again or a
            # non-recoverable error, the retry's outcome is terminal --
            # FAILED, with no further (third) retry attempted.
            assert finished.status is TaskStatus.FAILED
