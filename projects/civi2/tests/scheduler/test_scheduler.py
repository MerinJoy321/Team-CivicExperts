"""Unit tests for civicpilot.scheduler.scheduler.Scheduler (Task 4.3).

Covers submit()/run() wiring end to end using a fake async executor
(no real Tavily/Jina/etc. clients): single no-dependency task completion,
dependency ordering (B only starts after A completes), concurrency-limit
enforcement through the Scheduler's public interface, dependency-failure
skip resolving the dependent's future instead of hanging, and cancel() on
both a pending and a running task.

Timeout enforcement, retry policy, and telemetry hooks are intentionally
out of scope here -- they are covered by Task 5 and Task 6 respectively.
"""

from __future__ import annotations

import asyncio

import pytest

from civicpilot.scheduler.concurrency_gate import ConcurrencyGates
from civicpilot.scheduler.errors import RecoverableToolError
from civicpilot.scheduler.models import TaskStatus, ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.telemetry.telemetry_module import TelemetryModule
from civicpilot.telemetry.timing import now_s


def _make_task(task_id: str, *, depends_on: list[str] | None = None, **overrides) -> ToolTask:
    defaults = dict(
        task_id=task_id,
        category=ToolCategory.SEARCH,
        tool_name="fake_tool",
        params={},
        priority=5,
        timeout_ms=6000,
        depends_on=depends_on or [],
        agent_role="Researcher_Agent",
    )
    defaults.update(overrides)
    return ToolTask(**defaults)


async def fake_executor(task: ToolTask) -> str:
    await asyncio.sleep(task.params.get("duration", 0))
    return task.params.get("result", "ok")


def make_scheduler(limits: dict[ToolCategory, int] | None = None, **kwargs) -> Scheduler:
    limits = limits or {}
    return Scheduler(concurrency_limits=limits, executor=kwargs.pop("executor", fake_executor), **kwargs)


async def _run_until(scheduler: Scheduler, *futures: "asyncio.Future[ToolTask]", timeout: float = 2.0) -> None:
    """Starts scheduler.run() in the background and waits for all futures
    to resolve, then stops the loop and awaits its clean shutdown."""
    run_task = asyncio.create_task(scheduler.run())
    try:
        await asyncio.wait_for(asyncio.gather(*futures), timeout=timeout)
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=1.0)


class TestSingleTaskSubmission:
    @pytest.mark.asyncio
    async def test_single_no_dependency_task_resolves_to_completed_with_executor_result(self) -> None:
        scheduler = make_scheduler({ToolCategory.SEARCH: 4})
        task = _make_task("t1", params={"result": "search-result-1"})

        future = await scheduler.submit(task)
        await _run_until(scheduler, future)

        finished = future.result()
        assert finished.status is TaskStatus.COMPLETED
        assert finished.result == "search-result-1"
        assert finished.started_at is not None
        assert finished.completed_at is not None

    @pytest.mark.asyncio
    async def test_submit_returns_immediately_without_blocking_on_completion(self) -> None:
        scheduler = make_scheduler({ToolCategory.SEARCH: 4})
        task = _make_task("t1", params={"duration": 0.2})

        future = await scheduler.submit(task)

        # submit() must not have blocked on the task's 0.2s duration.
        assert not future.done()

        await _run_until(scheduler, future)
        assert future.result().status is TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_executor_failure_resolves_future_with_failed_status(self) -> None:
        async def failing_executor(task: ToolTask) -> str:
            raise ValueError("boom")

        scheduler = make_scheduler({ToolCategory.SEARCH: 4}, executor=failing_executor)
        task = _make_task("t1")

        future = await scheduler.submit(task)
        await _run_until(scheduler, future)

        finished = future.result()
        assert finished.status is TaskStatus.FAILED
        assert finished.error == "boom"


class TestDependencyOrdering:
    @pytest.mark.asyncio
    async def test_dependent_task_only_starts_after_its_dependency_completes(self) -> None:
        scheduler = make_scheduler({ToolCategory.SEARCH: 4})
        events: list[str] = []

        async def tracking_executor(task: ToolTask) -> str:
            events.append(f"{task.task_id}-start")
            await asyncio.sleep(task.params.get("duration", 0))
            events.append(f"{task.task_id}-end")
            return "ok"

        scheduler = make_scheduler({ToolCategory.SEARCH: 4}, executor=tracking_executor)
        a = _make_task("a", params={"duration": 0.1})
        b = _make_task("b", depends_on=["a"])

        future_a = await scheduler.submit(a)
        future_b = await scheduler.submit(b)
        await _run_until(scheduler, future_a, future_b)

        assert future_a.result().status is TaskStatus.COMPLETED
        assert future_b.result().status is TaskStatus.COMPLETED
        # b must not start until a has ended.
        assert events.index("a-end") < events.index("b-start")


class TestConcurrencyEnforcement:
    @pytest.mark.asyncio
    async def test_concurrency_limit_is_respected_end_to_end(self) -> None:
        scheduler = make_scheduler({ToolCategory.SEARCH: 2})
        active = 0
        max_active = 0
        lock = asyncio.Lock()

        async def tracking_executor(task: ToolTask) -> str:
            nonlocal active, max_active
            async with lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.1)
            async with lock:
                active -= 1
            return "ok"

        scheduler = make_scheduler({ToolCategory.SEARCH: 2}, executor=tracking_executor)
        tasks = [_make_task(f"t{i}") for i in range(5)]
        futures = [await scheduler.submit(t) for t in tasks]

        await _run_until(scheduler, *futures, timeout=3.0)

        assert max_active <= 2
        assert all(f.result().status is TaskStatus.COMPLETED for f in futures)


class TestDependencyFailureSkip:
    @pytest.mark.asyncio
    async def test_dependent_of_a_failed_task_resolves_to_skipped_without_hanging(self) -> None:
        scheduler = make_scheduler({ToolCategory.SEARCH: 4})
        a = _make_task("a", status=TaskStatus.FAILED)
        b = _make_task("b", depends_on=["a"])

        # 'a' is submitted pre-set to a terminal FAILED status directly
        # (simulating a dependency that already finished failing) rather
        # than going through the dispatch path itself, so only 'b's future
        # is awaited here.
        await scheduler.submit(a)
        future_b = await scheduler.submit(b)

        # The graph should skip 'b' on the very first resolve() pass
        # without ever dispatching it.
        await _run_until(scheduler, future_b)

        assert future_b.result().status is TaskStatus.SKIPPED


class TestTimeoutWrapper:
    @pytest.mark.asyncio
    async def test_executor_exceeding_timeout_resolves_to_timed_out_not_failed(self) -> None:
        async def slow_executor(task: ToolTask) -> str:
            await asyncio.sleep(0.5)
            return "should never get here"

        scheduler = make_scheduler({ToolCategory.SEARCH: 4}, executor=slow_executor)
        task = _make_task("t1", timeout_ms=100)

        future = await scheduler.submit(task)
        await _run_until(scheduler, future, timeout=2.0)

        finished = future.result()
        assert finished.status is TaskStatus.TIMED_OUT
        assert finished.retries_used == 0
        assert finished.started_at is not None
        assert finished.completed_at is not None

    @pytest.mark.asyncio
    async def test_executor_finishing_within_timeout_still_resolves_to_completed(self) -> None:
        async def quick_executor(task: ToolTask) -> str:
            await asyncio.sleep(0.05)
            return "on-time"

        scheduler = make_scheduler({ToolCategory.SEARCH: 4}, executor=quick_executor)
        task = _make_task("t1", timeout_ms=2000)

        future = await scheduler.submit(task)
        await _run_until(scheduler, future, timeout=2.0)

        finished = future.result()
        assert finished.status is TaskStatus.COMPLETED
        assert finished.result == "on-time"
        assert finished.started_at is not None
        assert finished.completed_at is not None


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_pending_task_resolves_future_with_cancelled_status(self) -> None:
        scheduler = make_scheduler({ToolCategory.SEARCH: 4})
        # Submitted but run() never started, so dependency resolution has
        # not happened yet -- the task is still PENDING (not yet ELIGIBLE
        # or dispatched), which is exactly the "not yet dispatched" case
        # cancel() should handle by setting CANCELLED directly.
        task = _make_task("t1")
        future = await scheduler.submit(task)

        assert task.status is TaskStatus.PENDING

        await scheduler.cancel("t1")
        finished = await asyncio.wait_for(future, timeout=1.0)

        assert finished.status is TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_running_task_cancels_the_underlying_asyncio_task(self) -> None:
        async def slow_executor(task: ToolTask) -> str:
            await asyncio.sleep(10)
            return "should never get here"

        scheduler = make_scheduler({ToolCategory.SEARCH: 4}, executor=slow_executor)
        task = _make_task("t1")
        future = await scheduler.submit(task)

        run_task = asyncio.create_task(scheduler.run())
        # Wait until the task actually starts running.
        for _ in range(100):
            registered = scheduler._graph.get("t1")
            if registered is not None and registered.status is TaskStatus.RUNNING:
                break
            await asyncio.sleep(0.01)
        assert registered.status is TaskStatus.RUNNING

        await scheduler.cancel("t1")
        finished = await asyncio.wait_for(future, timeout=1.0)

        assert finished.status is TaskStatus.CANCELLED

        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=1.0)


class TestRetryPolicy:
    """Covers Task 5.2's retry policy: exactly one retry on a
    `RecoverableToolError`, using the same timeout as the original attempt,
    and no retry at all for non-recoverable failures (Requirements 2.6,
    18.7, 18.8, 18.9)."""

    @pytest.mark.asyncio
    async def test_recoverable_error_then_success_resolves_to_completed_with_one_retry(self) -> None:
        attempts = 0

        async def flaky_executor(task: ToolTask) -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RecoverableToolError("transient network error")
            return "recovered"

        scheduler = make_scheduler({ToolCategory.SEARCH: 4}, executor=flaky_executor)
        task = _make_task("t1")

        future = await scheduler.submit(task)
        await _run_until(scheduler, future)

        finished = future.result()
        assert finished.status is TaskStatus.COMPLETED
        assert finished.result == "recovered"
        assert finished.retries_used == 1
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_recoverable_error_on_both_attempts_resolves_to_failed_with_exactly_one_retry(self) -> None:
        attempts = 0

        async def always_recoverable_executor(task: ToolTask) -> str:
            nonlocal attempts
            attempts += 1
            raise RecoverableToolError("503 service unavailable")

        scheduler = make_scheduler({ToolCategory.SEARCH: 4}, executor=always_recoverable_executor)
        task = _make_task("t1")

        future = await scheduler.submit(task)
        await _run_until(scheduler, future)

        finished = future.result()
        assert finished.status is TaskStatus.FAILED
        assert finished.retries_used == 1
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_non_recoverable_error_resolves_to_failed_with_no_retry(self) -> None:
        attempts = 0

        async def non_recoverable_executor(task: ToolTask) -> str:
            nonlocal attempts
            attempts += 1
            raise ValueError("bad url")

        scheduler = make_scheduler({ToolCategory.SEARCH: 4}, executor=non_recoverable_executor)
        task = _make_task("t1")

        future = await scheduler.submit(task)
        await _run_until(scheduler, future)

        finished = future.result()
        assert finished.status is TaskStatus.FAILED
        assert finished.error == "bad url"
        assert finished.retries_used == 0
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_retry_that_itself_times_out_resolves_to_timed_out_with_one_retry(self) -> None:
        attempts = 0

        async def recoverable_then_slow_executor(task: ToolTask) -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RecoverableToolError("connection reset")
            await asyncio.sleep(1.0)
            return "should never get here"

        scheduler = make_scheduler({ToolCategory.SEARCH: 4}, executor=recoverable_then_slow_executor)
        task = _make_task("t1", timeout_ms=100)

        future = await scheduler.submit(task)
        await _run_until(scheduler, future, timeout=2.0)

        finished = future.result()
        assert finished.status is TaskStatus.TIMED_OUT
        assert finished.retries_used == 1
        assert attempts == 2


class TestCancelAdditionalCases:
    """Additional Task 5.3 coverage for cancel() (Requirement 2.9) beyond
    the PENDING and RUNNING cases TestCancel already covers: a task that
    has resolved to ELIGIBLE and is sitting in its category's priority
    queue but has not yet been popped for dispatch (because the category's
    only concurrency slot is held by another task), and a no-op cancel()
    call on an already-terminal task."""

    @pytest.mark.asyncio
    async def test_cancel_eligible_but_queued_task_is_skipped_on_its_dispatch_turn(self) -> None:
        """'a' is submitted alone first and allowed to actually reach
        RUNNING (i.e. it has already acquired the category's only
        concurrency slot) before 'b' is submitted. Only once 'a' is
        confirmed RUNNING is 'b' submitted: 'b' resolves to ELIGIBLE and
        is pushed into the category's priority queue on the very next
        `_resolve_dependencies()` pass, but `_dispatch_eligible_tasks`
        finds `available_count(category) == 0` (the slot is already held
        by 'a') and leaves 'b' sitting in the queue -- ELIGIBLE, never
        popped, never in `_running_tasks`. This is exactly the "queued but
        not yet dispatched because concurrency is exhausted" state.
        cancel() must transition 'b' straight to CANCELLED via its
        "not running" branch, and `_dispatch_eligible_tasks` must discard
        it (without executing it) on any later pass once a slot frees up,
        per that method's own docstring."""

        async def slow_executor(task: ToolTask) -> str:
            await asyncio.sleep(task.params.get("duration", 0))
            return "ok"

        scheduler = make_scheduler({ToolCategory.SEARCH: 1}, executor=slow_executor)
        a = _make_task("a", params={"duration": 0.3})
        b = _make_task("b", params={"duration": 0.05})

        future_a = await scheduler.submit(a)

        run_task = asyncio.create_task(scheduler.run())
        try:
            for _ in range(200):
                reg_a = scheduler._graph.get("a")
                if reg_a is not None and reg_a.status is TaskStatus.RUNNING:
                    break
                await asyncio.sleep(0.01)
            assert reg_a.status is TaskStatus.RUNNING
            assert scheduler._gates.available_count(ToolCategory.SEARCH) == 0

            future_b = await scheduler.submit(b)

            # Give the loop a couple of iterations to resolve 'b' to
            # ELIGIBLE and attempt (and fail, due to the exhausted slot)
            # to dispatch it.
            for _ in range(20):
                reg_b = scheduler._graph.get("b")
                if reg_b is not None and reg_b.status is TaskStatus.ELIGIBLE:
                    break
                await asyncio.sleep(0.01)
            assert reg_b.status is TaskStatus.ELIGIBLE
            assert "b" not in scheduler._running_tasks

            await scheduler.cancel("b")
            finished_b = await asyncio.wait_for(future_b, timeout=1.0)
            assert finished_b.status is TaskStatus.CANCELLED
            assert "b" not in scheduler._running_tasks

            # 'a' is unaffected by 'b's cancellation and still completes
            # normally once its own duration elapses; the freed slot is
            # never claimed by 'b' since it was already CANCELLED.
            finished_a = await asyncio.wait_for(future_a, timeout=2.0)
            assert finished_a.status is TaskStatus.COMPLETED
        finally:
            scheduler.stop()
            await asyncio.wait_for(run_task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_cancel_on_already_completed_task_is_a_no_op(self) -> None:
        scheduler = make_scheduler({ToolCategory.SEARCH: 4})
        task = _make_task("t1", params={"result": "done"})
        future = await scheduler.submit(task)
        await _run_until(scheduler, future)

        finished = future.result()
        assert finished.status is TaskStatus.COMPLETED

        # A no-op: must not raise, must not change status, must not touch
        # the already-resolved future.
        await scheduler.cancel("t1")

        assert finished.status is TaskStatus.COMPLETED
        assert future.done()
        assert future.result().status is TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_cancel_on_already_cancelled_task_is_a_no_op(self) -> None:
        scheduler = make_scheduler({ToolCategory.SEARCH: 4})
        task = _make_task("t1")
        future = await scheduler.submit(task)

        await scheduler.cancel("t1")
        finished = await asyncio.wait_for(future, timeout=1.0)
        assert finished.status is TaskStatus.CANCELLED

        # Cancelling an already-CANCELLED task must not raise or change
        # anything further.
        await scheduler.cancel("t1")
        assert finished.status is TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_unknown_task_id_raises_key_error(self) -> None:
        scheduler = make_scheduler({ToolCategory.SEARCH: 4})
        with pytest.raises(KeyError):
            await scheduler.cancel("does-not-exist")


class TestFailureIsolation:
    """Covers Task 5.3's failure-isolation guarantee end to end through the
    full Scheduler dispatch loop (Requirements 2.7, 5.2, 19.1, 19.2): a
    failing (or timed-out) task must never cancel, pause, or delay
    independent sibling tasks dispatched concurrently alongside it."""

    @pytest.mark.asyncio
    async def test_one_sibling_failing_does_not_affect_other_concurrent_siblings(self) -> None:
        async def mixed_executor(task: ToolTask) -> str:
            await asyncio.sleep(task.params.get("duration", 0))
            if task.params.get("should_fail"):
                raise ValueError("boom")
            return task.params.get("result", "ok")

        # All 4 tasks are independent (no depends_on) and share a category
        # with enough concurrency slots for all of them to run at once, so
        # any effect of the failure on the others would show up as one of
        # them not reaching its expected terminal status.
        scheduler = make_scheduler({ToolCategory.SEARCH: 4}, executor=mixed_executor)
        failing = _make_task("failing", params={"duration": 0.05, "should_fail": True})
        siblings = [
            _make_task(f"sibling{i}", params={"duration": 0.1, "result": f"ok-{i}"})
            for i in range(3)
        ]

        futures = {t.task_id: await scheduler.submit(t) for t in [failing, *siblings]}
        await _run_until(scheduler, *futures.values(), timeout=2.0)

        assert futures["failing"].result().status is TaskStatus.FAILED
        assert futures["failing"].result().error == "boom"
        for i in range(3):
            finished = futures[f"sibling{i}"].result()
            assert finished.status is TaskStatus.COMPLETED
            assert finished.result == f"ok-{i}"

    @pytest.mark.asyncio
    async def test_timed_out_sibling_does_not_affect_other_concurrent_siblings(self) -> None:
        """Same guarantee, but for a TIMED_OUT sibling rather than FAILED --
        the timeout wrapper's own asyncio.wait_for cancellation must not
        propagate to, or delay, unrelated concurrent siblings."""

        async def mixed_executor(task: ToolTask) -> str:
            if task.params.get("hang"):
                await asyncio.sleep(10)
                return "should never get here"
            await asyncio.sleep(task.params.get("duration", 0))
            return task.params.get("result", "ok")

        scheduler = make_scheduler({ToolCategory.SEARCH: 4}, executor=mixed_executor)
        timing_out = _make_task("timing_out", params={"hang": True}, timeout_ms=100)
        siblings = [
            _make_task(f"sibling{i}", params={"duration": 0.1, "result": f"ok-{i}"})
            for i in range(3)
        ]

        futures = {t.task_id: await scheduler.submit(t) for t in [timing_out, *siblings]}
        await _run_until(scheduler, *futures.values(), timeout=2.0)

        assert futures["timing_out"].result().status is TaskStatus.TIMED_OUT
        for i in range(3):
            finished = futures[f"sibling{i}"].result()
            assert finished.status is TaskStatus.COMPLETED
            assert finished.result == f"ok-{i}"

    @pytest.mark.asyncio
    async def test_fast_failing_sibling_does_not_delay_a_slower_successful_sibling(self) -> None:
        """Timing-based check for Requirement 5.2: an independent sibling
        that fails fast must not push back the completion time of a
        slower, independent sibling running concurrently alongside it."""

        async def mixed_executor(task: ToolTask) -> str:
            await asyncio.sleep(task.params.get("duration", 0))
            if task.params.get("should_fail"):
                raise ValueError("fast failure")
            return "ok"

        scheduler = make_scheduler({ToolCategory.SEARCH: 4}, executor=mixed_executor)
        fast_failure = _make_task("fast_failure", params={"duration": 0.02, "should_fail": True})
        slow_success = _make_task("slow_success", params={"duration": 0.3})

        start = now_s()
        future_fail = await scheduler.submit(fast_failure)
        future_slow = await scheduler.submit(slow_success)
        await _run_until(scheduler, future_fail, future_slow, timeout=2.0)
        total_elapsed = now_s() - start

        finished_fail = future_fail.result()
        finished_slow = future_slow.result()
        assert finished_fail.status is TaskStatus.FAILED
        assert finished_slow.status is TaskStatus.COMPLETED

        # The slow sibling's own measured duration is unaffected by the
        # fast sibling's failure.
        slow_duration = finished_slow.completed_at - finished_slow.started_at
        assert slow_duration < 0.3 + 0.2  # generous tolerance for scheduling overhead

        # Total wall-clock time for both is bounded by the slower task's
        # own duration plus overhead -- not the sum of both durations,
        # which would indicate the fast failure blocked or delayed the
        # slow task's dispatch or completion.
        assert total_elapsed < 0.3 + 0.2


class _CountingTelemetry(TelemetryModule):
    """A real `TelemetryModule` that also records the sequence of task_ids
    each hook was called for, so tests can assert exact call counts/pairing
    on top of the real running-count/batch accounting."""

    def __init__(self) -> None:
        super().__init__()
        self.start_calls: list[str] = []
        self.complete_calls: list[str] = []

    def on_task_start(self, task: ToolTask) -> None:
        self.start_calls.append(task.task_id)
        super().on_task_start(task)

    def on_task_complete(self, task: ToolTask) -> None:
        self.complete_calls.append(task.task_id)
        super().on_task_complete(task)


class TestTelemetryWiring:
    """Covers Task 6.3: wiring `TelemetryModule.on_task_start`/
    `on_task_complete` into the Scheduler's `_execute` lifecycle at the
    exact RUNNING-transition and completion points, with correct pairing
    across the SKIPPED, cancel-before-running, and cancel-while-running
    paths (Requirements 2.8, 2.10)."""

    @pytest.mark.asyncio
    async def test_normal_completed_task_calls_both_hooks_exactly_once(self) -> None:
        telemetry = _CountingTelemetry()
        scheduler = Scheduler(
            concurrency_limits={ToolCategory.SEARCH: 4},
            executor=fake_executor,
            telemetry=telemetry,
        )
        task = _make_task("t1", params={"result": "ok"})

        future = await scheduler.submit(task)
        await _run_until(scheduler, future)

        finished = future.result()
        assert finished.status is TaskStatus.COMPLETED
        assert telemetry.start_calls == ["t1"]
        assert telemetry.complete_calls == ["t1"]

    @pytest.mark.asyncio
    async def test_concurrent_independent_tasks_form_one_batch(self) -> None:
        telemetry = _CountingTelemetry()
        scheduler = Scheduler(
            concurrency_limits={ToolCategory.SEARCH: 4},
            executor=fake_executor,
            telemetry=telemetry,
        )
        tasks = [_make_task(f"t{i}", params={"duration": 0.1}) for i in range(4)]
        futures = [await scheduler.submit(t) for t in tasks]

        await _run_until(scheduler, *futures, timeout=2.0)

        assert all(f.result().status is TaskStatus.COMPLETED for f in futures)
        assert sorted(telemetry.start_calls) == sorted(telemetry.complete_calls) == [
            f"t{i}" for i in range(4)
        ]
        assert telemetry.parallel_batches == 1
        [batch] = telemetry.batches(ToolCategory.SEARCH)
        assert batch.batch_size == 4

    @pytest.mark.asyncio
    async def test_skipped_task_never_triggers_either_hook(self) -> None:
        telemetry = _CountingTelemetry()
        scheduler = Scheduler(
            concurrency_limits={ToolCategory.SEARCH: 4},
            executor=fake_executor,
            telemetry=telemetry,
        )
        a = _make_task("a", status=TaskStatus.FAILED)
        b = _make_task("b", depends_on=["a"])

        await scheduler.submit(a)
        future_b = await scheduler.submit(b)
        await _run_until(scheduler, future_b)

        assert future_b.result().status is TaskStatus.SKIPPED
        assert telemetry.start_calls == []
        assert telemetry.complete_calls == []

    @pytest.mark.asyncio
    async def test_task_cancelled_while_pending_never_triggers_either_hook(self) -> None:
        telemetry = _CountingTelemetry()
        scheduler = Scheduler(
            concurrency_limits={ToolCategory.SEARCH: 4},
            executor=fake_executor,
            telemetry=telemetry,
        )
        task = _make_task("t1")
        future = await scheduler.submit(task)

        assert task.status is TaskStatus.PENDING

        await scheduler.cancel("t1")
        finished = await asyncio.wait_for(future, timeout=1.0)

        assert finished.status is TaskStatus.CANCELLED
        assert telemetry.start_calls == []
        assert telemetry.complete_calls == []

    @pytest.mark.asyncio
    async def test_task_cancelled_while_running_still_pairs_both_hooks(self) -> None:
        async def slow_executor(task: ToolTask) -> str:
            await asyncio.sleep(10)
            return "should never get here"

        telemetry = _CountingTelemetry()
        scheduler = Scheduler(
            concurrency_limits={ToolCategory.SEARCH: 4},
            executor=slow_executor,
            telemetry=telemetry,
        )
        task = _make_task("t1")
        future = await scheduler.submit(task)

        run_task = asyncio.create_task(scheduler.run())
        try:
            for _ in range(100):
                registered = scheduler._graph.get("t1")
                if registered is not None and registered.status is TaskStatus.RUNNING:
                    break
                await asyncio.sleep(0.01)
            assert registered.status is TaskStatus.RUNNING
            assert telemetry.start_calls == ["t1"]

            await scheduler.cancel("t1")
            finished = await asyncio.wait_for(future, timeout=1.0)

            assert finished.status is TaskStatus.CANCELLED
            # Paired: on_task_start was called once RUNNING was reached,
            # so on_task_complete must still fire once cancellation
            # resolves, or the TelemetryModule's running count would leak.
            assert telemetry.start_calls == ["t1"]
            assert telemetry.complete_calls == ["t1"]
            assert telemetry.maximum_concurrency(ToolCategory.SEARCH) == 1
        finally:
            scheduler.stop()
            await asyncio.wait_for(run_task, timeout=1.0)

    @pytest.mark.asyncio
    async def test_scheduler_without_telemetry_works_unchanged(self) -> None:
        scheduler = make_scheduler({ToolCategory.SEARCH: 4})
        task = _make_task("t1", params={"result": "no-telemetry"})

        future = await scheduler.submit(task)
        await _run_until(scheduler, future)

        finished = future.result()
        assert finished.status is TaskStatus.COMPLETED
        assert finished.result == "no-telemetry"
