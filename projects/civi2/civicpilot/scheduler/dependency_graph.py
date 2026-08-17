"""Dependency graph resolution for `ToolTask`s (Requirements 2.2, 2.11).

This module implements the piece of Scheduler behavior the design calls
"the dependency graph": given a collection of `ToolTask`s (keyed by
`task_id`) and their current `status`, `DependencyGraph.resolve()` decides
which `PENDING` tasks should move to `ELIGIBLE` or `SKIPPED`.

Two rules apply, exactly as described in the design's "Dependency graph"
bullet under "Design notes mapped to acceptance criteria":

- A task moves `PENDING -> ELIGIBLE` the instant every entry in
  `depends_on` reaches `COMPLETED` (Requirement 2.2). A task with an empty
  `depends_on` list is immediately eligible -- there is nothing to wait
  on.
- If any dependency reaches `FAILED`, `TIMED_OUT`, or `CANCELLED` (the
  `SKIP_PROPAGATING_STATUSES` set defined in `civicpilot.scheduler.models`),
  the dependent task is marked `SKIPPED` without ever executing
  (Requirement 2.11). This propagates transitively: if A fails, B (which
  depends on A) is skipped, and C (which depends on B, but never directly
  on A) is also skipped -- all within a single `resolve()` call.

This module only decides *status transitions* driven by dependency state.
It does not run tasks, gate concurrency, or apply priority ordering --
those are separate Scheduler concerns implemented in later tasks.
"""

from __future__ import annotations

from typing import Iterable, Optional

from civicpilot.scheduler.models import SKIP_PROPAGATING_STATUSES, TaskStatus, ToolTask
from civicpilot.telemetry.timing import now_s

__all__ = ["CycleDetectedError", "DependencyGraph"]


class CycleDetectedError(Exception):
    """Raised when `depends_on` edges among registered tasks form a cycle.

    A cyclic dependency graph can never be fully resolved (each task in
    the cycle would wait forever on another task in the same cycle), so
    cycles are rejected as soon as they are detectable rather than being
    silently left `PENDING` forever.
    """


class DependencyGraph:
    """Holds a collection of `ToolTask`s keyed by `task_id` and resolves
    `PENDING -> ELIGIBLE` / `PENDING -> SKIPPED` status transitions based
    on the current status of each task's declared dependencies.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, ToolTask] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def add_task(self, task: ToolTask) -> None:
        """Registers (or replaces) a single task in the graph.

        Raises `CycleDetectedError` if adding this task completes a cycle
        among the currently-registered tasks' `depends_on` edges.
        """
        self._tasks[task.task_id] = task
        self._check_for_cycles()

    def add_tasks(self, tasks: Iterable[ToolTask]) -> None:
        """Registers (or replaces) multiple tasks in one call.

        Cycle detection runs once after all tasks have been added, so a
        batch of mutually-referencing tasks (e.g. tasks that reference
        sibling task_ids not yet known individually) is checked as a
        whole rather than rejecting valid forward references mid-batch.
        """
        for task in tasks:
            self._tasks[task.task_id] = task
        self._check_for_cycles()

    def get(self, task_id: str) -> Optional[ToolTask]:
        """Returns the registered task for `task_id`, or `None`."""
        return self._tasks.get(task_id)

    @property
    def tasks(self) -> dict[str, ToolTask]:
        """All registered tasks, keyed by `task_id`."""
        return self._tasks

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self) -> list[ToolTask]:
        """Scans all `PENDING` tasks and applies the eligibility/skip
        rules described in the module docstring.

        Runs repeated internal passes over the registered tasks until a
        full pass produces no further transition, so a chain of
        transitive skips (A fails -> B skipped -> C skipped, even though
        C only declares a dependency on B) is fully resolved within this
        single call -- no task is left `PENDING` when it should already
        be `SKIPPED` or `ELIGIBLE` due to the current state of its
        dependencies, however many levels deep that state must propagate.

        Returns the list of tasks whose `status` changed during this
        call, in the order they were transitioned.
        """
        changed: list[ToolTask] = []
        made_progress = True
        while made_progress:
            made_progress = False
            for task in self._tasks.values():
                if task.status is not TaskStatus.PENDING:
                    continue

                if not task.depends_on:
                    task.status = TaskStatus.ELIGIBLE
                    task.dependency_satisfied_at = now_s()
                    changed.append(task)
                    made_progress = True
                    continue

                dep_statuses = [
                    self._tasks[dep_id].status
                    for dep_id in task.depends_on
                    if dep_id in self._tasks
                ]

                # A dependency that is itself SKIPPED will never reach
                # COMPLETED, so it must propagate the skip just like the
                # FAILED/TIMED_OUT/CANCELLED statuses in
                # SKIP_PROPAGATING_STATUSES -- this is what makes
                # transitive propagation work across multiple levels
                # (A fails -> B skipped -> C, which only depends on B,
                # is skipped too).
                if any(
                    status in SKIP_PROPAGATING_STATUSES or status is TaskStatus.SKIPPED
                    for status in dep_statuses
                ):
                    task.status = TaskStatus.SKIPPED
                    changed.append(task)
                    made_progress = True
                    continue

                # Only every declared dependency being known AND COMPLETED
                # satisfies eligibility -- a missing/unregistered
                # dependency id can never be COMPLETED, so it simply keeps
                # the task PENDING rather than raising here.
                if len(dep_statuses) == len(task.depends_on) and all(
                    status is TaskStatus.COMPLETED for status in dep_statuses
                ):
                    task.status = TaskStatus.ELIGIBLE
                    task.dependency_satisfied_at = now_s()
                    changed.append(task)
                    made_progress = True

        return changed

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def _check_for_cycles(self) -> None:
        """Raises `CycleDetectedError` if the registered tasks' `depends_on`
        edges contain a cycle.

        Uses the standard three-color DFS (white/gray/black): a node
        revisited while still gray (on the current DFS stack) means the
        edges form a cycle. Dependency ids that are not (yet) registered
        tasks are treated as leaves -- they cannot participate in a cycle
        until they are themselves registered with their own `depends_on`.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {task_id: WHITE for task_id in self._tasks}

        def visit(task_id: str, path: list[str]) -> None:
            color[task_id] = GRAY
            task = self._tasks.get(task_id)
            for dep_id in (task.depends_on if task else []):
                if dep_id not in self._tasks:
                    continue
                dep_color = color.get(dep_id, WHITE)
                if dep_color == GRAY:
                    cycle_start = path.index(dep_id) if dep_id in path else 0
                    cycle = path[cycle_start:] + [dep_id]
                    raise CycleDetectedError(
                        "Cycle detected in depends_on graph: "
                        + " -> ".join(cycle)
                    )
                if dep_color == WHITE:
                    visit(dep_id, path + [dep_id])
            color[task_id] = BLACK

        for task_id in list(self._tasks):
            if color[task_id] == WHITE:
                visit(task_id, [task_id])
