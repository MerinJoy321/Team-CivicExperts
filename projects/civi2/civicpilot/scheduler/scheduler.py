"""The `Scheduler`: `submit()`, `run()`, and `cancel()` (Task 4.3).

This module wires together the three pieces built in Tasks 3 and 4 into the
actual execution engine the design calls the Scheduler:

- `civicpilot.scheduler.dependency_graph.DependencyGraph` (Task 3.2):
  decides `PENDING -> ELIGIBLE` / `PENDING -> SKIPPED` transitions.
- `civicpilot.scheduler.priority_queue.CategoryPriorityQueue` (Task 4.1):
  one per `ToolCategory`, dispatch ordering by `(priority,
  dependency_satisfied_at)`.
- `civicpilot.scheduler.concurrency_gate.ConcurrencyGates` (Task 4.2): one
  `asyncio.Semaphore` per `ToolCategory`, bounding concurrent execution.

Timeout enforcement (Task 5.1), retry policy (Task 5.2), and telemetry
hooks (Task 6) are layered on top of the dependency resolution + priority
dispatch + concurrency gating wiring described above. Retry policy:
a tool executor signals a retryable failure by raising
`civicpilot.scheduler.errors.RecoverableToolError`; the Scheduler then
retries that task's executor exactly once using the same `timeout_ms`
budget as the original attempt, marking `FAILED` (or `TIMED_OUT`, if the
retry itself times out) if the retry also fails. Any other exception type
is treated as non-retryable and goes straight to `FAILED` with no retry
(Requirement 2.6, 18.7-18.9).

Pluggable executor
-------------------
There is no tool-executor registry yet (that lands properly in Phase 3,
Task 9+), so the Scheduler accepts a pluggable async executor at
construction time instead of hardcoding Tavily/Jina/etc. clients:

- A single callable ``executor: Callable[[ToolTask], Awaitable[Any]]``,
  used for every task regardless of `tool_name`; or
- A ``dict[str, Callable[[ToolTask], Awaitable[Any]]]`` keyed by
  `tool_name`, so different fake/stub/real tool implementations can be
  registered per tool now and swapped for real Tavily/Jina/etc. clients
  later without changing the Scheduler's public interface.

Concurrency limits construction
--------------------------------
The constructor accepts either:

- ``concurrency_limits: Mapping[ToolCategory, int]`` -- a (possibly
  partial) mapping of category to limit. Any `ToolCategory` member not
  present is filled in with
  `civicpilot.scheduler.concurrency_gate.DEFAULT_LIMIT_FOR_UNSPECIFIED_CATEGORIES`,
  the same default `ConcurrencyGates.from_concurrency_config` uses. This
  keeps test/call-site setup minimal -- callers only need to specify the
  categories they actually care about bounding.
- ``gates: ConcurrencyGates`` -- a pre-built instance, e.g. one already
  constructed via `ConcurrencyGates.from_concurrency_config(settings.concurrency)`
  by the eventual application wiring. Useful when the caller wants full
  control over every category's limit (e.g. production wiring reusing the
  loaded `.env` settings for `SEARCH`/`FETCH`/`VERIFY`).

Exactly one of these two must be supplied.

Scheduling loop shape (no busy-poll)
-------------------------------------
`run()` is driven by an internal `asyncio.Event` that `submit()` and every
task completion (`_execute`'s `finally` block) set to wake the loop
immediately when there is new work to consider. A short timeout
(`_POLL_INTERVAL_S`) on the event wait is kept purely as a safety net --
normal operation never relies on that timeout firing, since every state
change that could make a task eligible for dispatch is itself an explicit
wake source.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Optional, Union

from civicpilot.scheduler.concurrency_gate import (
    DEFAULT_LIMIT_FOR_UNSPECIFIED_CATEGORIES,
    ConcurrencyGates,
)
from civicpilot.scheduler.dependency_graph import DependencyGraph
from civicpilot.scheduler.errors import RecoverableToolError
from civicpilot.scheduler.models import TaskStatus, ToolCategory, ToolTask
from civicpilot.scheduler.priority_queue import CategoryPriorityQueue

if TYPE_CHECKING:
    from civicpilot.telemetry.telemetry_module import TelemetryModule
from civicpilot.telemetry.timing import now_s

__all__ = ["Scheduler", "Executor", "ExecutorRegistry"]

#: A single tool executor: given a `ToolTask`, awaits and returns its result
#: (or raises on failure).
Executor = Callable[[ToolTask], Awaitable[Any]]

#: Either one executor used for every task, or a mapping from `tool_name`
#: to the executor that should handle that tool.
ExecutorRegistry = Union[Executor, Mapping[str, Executor]]

#: Terminal statuses -- once a task reaches one of these, `cancel()` treats
#: it as already finished and does nothing further.
_TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.TIMED_OUT,
        TaskStatus.CANCELLED,
        TaskStatus.SKIPPED,
    }
)

#: Safety-net poll interval for the run() loop's wait -- see module
#: docstring's "Scheduling loop shape" section. Normal wakeups happen well
#: before this timeout via explicit `_wake()` calls.
_POLL_INTERVAL_S = 0.05


class Scheduler:
    """Registers, dispatches, executes, and tracks `ToolTask`s end to end.

    See the module docstring for the pluggable-executor and
    concurrency-limits construction options. `submit()` never blocks on
    task completion -- it registers the task and returns a `Future`
    immediately. `run()` is the main scheduling loop and is intended to be
    started once as a background task (e.g.
    ``asyncio.create_task(scheduler.run())``); `submit()` may be called
    both before and after `run()` has started.
    """

    def __init__(
        self,
        concurrency_limits: Optional[Mapping[ToolCategory, int]] = None,
        *,
        gates: Optional[ConcurrencyGates] = None,
        executor: ExecutorRegistry,
        telemetry: Optional[TelemetryModule] = None,
    ) -> None:
        if (concurrency_limits is None) == (gates is None):
            raise ValueError(
                "Scheduler requires exactly one of concurrency_limits or gates"
            )

        if gates is not None:
            self._gates = gates
        else:
            full_limits: dict[ToolCategory, int] = dict(concurrency_limits)  # type: ignore[arg-type]
            for category in ToolCategory:
                full_limits.setdefault(category, DEFAULT_LIMIT_FOR_UNSPECIFIED_CATEGORIES)
            self._gates = ConcurrencyGates(full_limits)

        self._executor = executor
        self._telemetry = telemetry
        self._graph = DependencyGraph()
        self._queues: dict[ToolCategory, CategoryPriorityQueue] = {
            category: CategoryPriorityQueue(category) for category in ToolCategory
        }
        self._futures: dict[str, "asyncio.Future[ToolTask]"] = {}
        self._running_tasks: dict[str, "asyncio.Task[None]"] = {}
        self._wake_event = asyncio.Event()
        self._stopping = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(self, task: ToolTask) -> "asyncio.Future[ToolTask]":
        """Registers `task` and returns a `Future` resolving on completion.

        Registers the task in the dependency graph (raising
        `CycleDetectedError`, propagated to the caller, if it would
        introduce a cycle among already-registered tasks), then creates
        and stores a `Future` keyed by `task.task_id` that will be
        resolved with the completed `ToolTask` once it reaches a terminal
        status. Returns that future immediately -- this method never
        awaits the task's completion itself.
        """
        self._graph.add_task(task)  # may raise CycleDetectedError
        future: "asyncio.Future[ToolTask]" = asyncio.get_event_loop().create_future()
        self._futures[task.task_id] = future
        self._wake()
        return future

    async def cancel(self, task_id: str) -> None:
        """Cancels a pending, eligible, or running task; sets status=cancelled.

        For a task not yet dispatched (`PENDING` or `ELIGIBLE`, including
        one still sitting in its category's priority queue), sets
        `status=CANCELLED` directly and resolves its future immediately.
        For a `RUNNING` task, cancels the underlying `asyncio.Task`
        (best-effort) and awaits it so `status=CANCELLED` and the future
        resolution (both performed by `_execute`'s cancellation handling)
        have completed before this method returns. A no-op for a task
        that has already reached a terminal status, or an unregistered
        `task_id` raises `KeyError`.
        """
        task = self._graph.get(task_id)
        if task is None:
            raise KeyError(f"Unknown task_id: {task_id!r}")
        if task.status in _TERMINAL_STATUSES:
            return

        running = self._running_tasks.get(task_id)
        if running is not None:
            running.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running
        else:
            task.status = TaskStatus.CANCELLED
            self._resolve_future(task)
        self._wake()

    async def run(self) -> None:
        """Main scheduling loop.

        Each iteration: advances `PENDING -> ELIGIBLE`/`SKIPPED`
        transitions via `DependencyGraph.resolve()`, moves newly-eligible
        tasks into their category's priority queue (resolving the future
        of any newly-skipped task immediately, since a `SKIPPED` task
        never executes and callers awaiting it must not hang forever),
        then dispatches as many queued-and-eligible tasks as available
        concurrency slots allow, for every category. Sleeps on an internal
        `asyncio.Event` (set by `submit()` and by task completions) between
        iterations rather than busy-polling. Exits after `stop()` is
        called (or after the surrounding `asyncio.Task` is cancelled).
        """
        self._stopping = False
        while not self._stopping:
            self._resolve_dependencies()
            self._dispatch_eligible_tasks()
            self._wake_event.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake_event.wait(), timeout=_POLL_INTERVAL_S)

    def stop(self) -> None:
        """Requests the `run()` loop to exit after its current iteration."""
        self._stopping = True
        self._wake()

    # ------------------------------------------------------------------
    # Internal: dependency resolution + dispatch
    # ------------------------------------------------------------------

    def _wake(self) -> None:
        self._wake_event.set()

    def _resolve_dependencies(self) -> None:
        """Advances dependency-driven transitions and reacts to them.

        Newly `ELIGIBLE` tasks are pushed into their category's priority
        queue so `_dispatch_eligible_tasks` can consider them. Newly
        `SKIPPED` tasks never execute, so their future is resolved right
        here rather than waiting for a dispatch path that will never be
        taken for them.
        """
        for task in self._graph.resolve():
            if task.status is TaskStatus.ELIGIBLE:
                self._queues[task.category].push(task)
            elif task.status is TaskStatus.SKIPPED:
                self._resolve_future(task)

    def _dispatch_eligible_tasks(self) -> None:
        """Dispatches as many queued-eligible tasks as concurrency allows.

        For each category, pops tasks off that category's priority queue
        while a slot is available and dispatches them, one
        `asyncio.Task` per dispatched `ToolTask`. A popped task whose
        status is no longer `ELIGIBLE` (e.g. it was cancelled while still
        queued) is discarded without dispatch -- its future was already
        resolved by `cancel()`.
        """
        for category, queue in self._queues.items():
            while not queue.is_empty() and self._gates.available_count(category) > 0:
                task = queue.pop()
                if task.status is not TaskStatus.ELIGIBLE:
                    continue
                self._dispatch(task)

    def _dispatch(self, task: ToolTask) -> None:
        asyncio_task = asyncio.create_task(self._execute(task))
        self._running_tasks[task.task_id] = asyncio_task

    async def _execute(self, task: ToolTask) -> None:
        """Runs one task end to end: acquire slot, execute, resolve future.

        Acquires the category's concurrency slot, sets `status=RUNNING`
        and `started_at`, then (if a `TelemetryModule` was supplied at
        construction) calls `self._telemetry.on_task_start(task)`
        immediately afterward, so the hook observes the fully-updated
        RUNNING task state (Requirement 2.8). It then runs the retry-aware
        attempt loop (`_run_attempts`), which invokes the resolved executor
        at most twice (the original attempt, plus exactly one retry on a
        `RecoverableToolError`, per Requirement 2.6/18.7-18.9) and leaves
        `task.status`/`task.result`/`task.error`/`task.retries_used` set to
        their final outcome. If cancelled at any point (including while
        still waiting for a concurrency slot -- in which case `RUNNING` is
        never reached and `on_task_start` is never called), sets
        `status=CANCELLED` instead and re-raises so the cancelling
        `asyncio.Task.cancel()`/await in `cancel()` observes it.
        `completed_at` is set, the entry
        in `_running_tasks` is removed, and (only if `on_task_start` was
        actually called for this task, tracked via the local
        `started_telemetry` flag) `self._telemetry.on_task_complete(task)`
        is called, so every `on_task_start` is paired with exactly one
        `on_task_complete` regardless of success, failure, timeout, or
        cancellation (Requirement 2.10) -- a task cancelled before ever
        acquiring its concurrency slot never calls either hook. The task's
        future is resolved in every case (success, failure, timeout, or
        cancellation) via the `finally` block, so a caller awaiting the
        future can never hang.
        """
        started_telemetry = False
        try:
            async with self._gates.acquire(task.category):
                task.status = TaskStatus.RUNNING
                task.started_at = now_s()
                if self._telemetry is not None:
                    self._telemetry.on_task_start(task)
                    started_telemetry = True
                await self._run_attempts(task)
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            raise
        finally:
            task.completed_at = now_s()
            self._running_tasks.pop(task.task_id, None)
            if started_telemetry and self._telemetry is not None:
                self._telemetry.on_task_complete(task)
            self._resolve_future(task)
            self._wake()

    async def _run_attempts(self, task: ToolTask) -> None:
        """Executes `task` with the Requirement 2.6/18.7-18.9 retry policy.

        First attempt: on success, `status=COMPLETED`/`result` set. On
        `asyncio.TimeoutError`, `status=TIMED_OUT` -- terminal, no retry
        (Requirement 2.5, 18.6), even though a timeout is also how a
        `RecoverableToolError` scenario's retry attempt can end. On a
        `RecoverableToolError`, retries exactly once, re-invoking the
        executor with the *same* `timeout_ms` budget and incrementing
        `task.retries_used` to 1 for that retry (Requirement 18.7). Any
        other exception on the first attempt is non-recoverable: straight
        to `FAILED` with `retries_used` left at 0 (Requirement 18.8).

        The retry attempt's own outcome is terminal regardless of its
        failure mode: a second `RecoverableToolError`, a different
        exception, or a timeout all resolve to `FAILED` (or `TIMED_OUT` if
        the retry specifically times out) with no further retry
        (Requirement 18.9) -- `retries_used` never exceeds 1.
        """
        try:
            result = await self._invoke_executor(task)
        except asyncio.TimeoutError:
            task.status = TaskStatus.TIMED_OUT
        except RecoverableToolError:
            task.retries_used = 1
            try:
                retry_result = await self._invoke_executor(task)
            except asyncio.TimeoutError:
                task.status = TaskStatus.TIMED_OUT
            except Exception as exc:  # noqa: BLE001 - retry exhausted, isolate failure
                task.status = TaskStatus.FAILED
                task.error = str(exc)
            else:
                task.status = TaskStatus.COMPLETED
                task.result = retry_result
        except Exception as exc:  # noqa: BLE001 - non-recoverable, isolate failure
            task.status = TaskStatus.FAILED
            task.error = str(exc)
        else:
            task.status = TaskStatus.COMPLETED
            task.result = result

    async def _invoke_executor(self, task: ToolTask) -> Any:
        """Runs the resolved executor once, bounded by `task.timeout_ms`.

        Used for both the original attempt and the single retry attempt
        so both share the exact same timeout budget, per Requirement
        18.7 ("using the same timeout as the original attempt").
        """
        executor = self._resolve_executor(task)
        return await asyncio.wait_for(executor(task), timeout=task.timeout_ms / 1000)

    def _resolve_executor(self, task: ToolTask) -> Executor:
        if isinstance(self._executor, Mapping):
            try:
                return self._executor[task.tool_name]
            except KeyError as exc:
                raise LookupError(
                    f"No executor registered for tool_name={task.tool_name!r}"
                ) from exc
        return self._executor

    def _resolve_future(self, task: ToolTask) -> None:
        future = self._futures.get(task.task_id)
        if future is not None and not future.done():
            future.set_result(task)
