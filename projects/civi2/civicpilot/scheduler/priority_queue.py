"""Per-category priority heap with tie-break ordering (Requirement 2.4).

This module implements the piece of Scheduler behavior the design calls
"the priority queue": for a single `ToolCategory`, `CategoryPriorityQueue`
holds every `ELIGIBLE` `ToolTask` competing for that category's
concurrency slots and dispatches them in the order described in the
design's "Priority queue" bullet under "Design notes mapped to acceptance
criteria":

    each category owns a binary heap ordered by
    (priority, dependency_satisfied_at) -- lower priority number first,
    ties broken by earliest dependency-satisfaction timestamp (FIFO among
    equals).

`ToolTask` is a mutable dataclass with no natural ordering (and no
`__lt__`), so this module never compares `ToolTask` instances directly.
Instead, `push()` stores a tuple of
`(priority, dependency_satisfied_at_key, insertion_sequence, task)` on a
`heapq` heap. `heapq` compares tuples element-by-element and only moves on
to the next element when the previous ones are equal, so:

- `priority` (lower first) is the primary sort key.
- `dependency_satisfied_at_key` (earlier first) is the tie-break for equal
  priority.
- `insertion_sequence` (an ever-increasing counter) is the final tie-break
  for perfect determinism when both `priority` and
  `dependency_satisfied_at` are equal -- this also guarantees the
  `task` element is never reached during comparison, so `ToolTask` itself
  never needs to be orderable.

Handling of `dependency_satisfied_at is None`: an `ELIGIBLE` task should
always have `dependency_satisfied_at` set by `DependencyGraph.resolve()`
(see `civicpilot.scheduler.dependency_graph`), since that is the exact
transition that sets it. This module does not assume that invariant holds
elsewhere, though, and does not crash if it is violated: a `None` value is
treated as "ready immediately" by substituting `float("-inf")` as its sort
key, so such a task sorts as the earliest possible `dependency_satisfied_at`
among its priority tier (i.e. it is dispatched before any task in the same
priority tier that has a real timestamp) rather than raising or comparing
`None` against a `float`, which would raise `TypeError` in Python.

This module only implements the priority/FIFO ordering itself. It does not
gate concurrency (Task 4.2) or run tasks (Task 4.3) -- those are separate
Scheduler concerns implemented in later tasks.
"""

from __future__ import annotations

import heapq
import itertools

from civicpilot.scheduler.models import ToolCategory, ToolTask

__all__ = ["CategoryPriorityQueue", "PriorityQueueEmptyError"]


class PriorityQueueEmptyError(Exception):
    """Raised by `CategoryPriorityQueue.pop()` when the queue is empty.

    `pop()` raises rather than returning `None` because `None` is not
    distinguishable from a legitimate (if unusual) future return value,
    and a caller that forgets to check for emptiness should fail loudly
    (an exception) rather than silently receiving `None` and crashing
    later with a less obvious error deeper in the call stack.
    """


class CategoryPriorityQueue:
    """A binary heap of `ELIGIBLE` `ToolTask`s scoped to one `ToolCategory`.

    Ordering (Requirement 2.4): lower `priority` integer value is
    dispatched first (1 = highest priority, dispatched before 10). Among
    tasks with equal `priority`, the task with the earlier
    `dependency_satisfied_at` timestamp is dispatched first (FIFO
    tie-break). If both `priority` and `dependency_satisfied_at` are
    equal, the task pushed first is dispatched first (insertion-order
    tie-break), guaranteeing a fully deterministic dispatch order.

    One instance of this class is intended to be owned per `ToolCategory`
    by the Scheduler (Task 4.3); the `category` this instance is scoped to
    is recorded for diagnostics/`repr` only and is not itself part of the
    ordering (a `CategoryPriorityQueue` does not enforce that every pushed
    task actually belongs to `category` -- that invariant is the calling
    Scheduler's responsibility).
    """

    def __init__(self, category: ToolCategory) -> None:
        self.category = category
        self._heap: list[tuple[int, float, int, ToolTask]] = []
        self._counter = itertools.count()

    def push(self, task: ToolTask) -> None:
        """Adds an `ELIGIBLE` task to this category's priority heap.

        The task's `priority` and `dependency_satisfied_at` are read at
        push time and baked into the heap entry's sort key; if either
        field is mutated after `push()` is called, the change has no
        effect on this task's position in the heap (it would only affect
        a task pushed afresh with the updated values).
        """
        dependency_satisfied_at_key = (
            float("-inf")
            if task.dependency_satisfied_at is None
            else task.dependency_satisfied_at
        )
        entry = (task.priority, dependency_satisfied_at_key, next(self._counter), task)
        heapq.heappush(self._heap, entry)

    def pop(self) -> ToolTask:
        """Removes and returns the highest-priority-then-earliest-ready task.

        Raises `PriorityQueueEmptyError` if the queue is empty.
        """
        if not self._heap:
            raise PriorityQueueEmptyError(
                f"pop() called on an empty CategoryPriorityQueue for category "
                f"{self.category!r}"
            )
        _, _, _, task = heapq.heappop(self._heap)
        return task

    def peek(self) -> ToolTask:
        """Returns (without removing) the task `pop()` would return next.

        Raises `PriorityQueueEmptyError` if the queue is empty.
        """
        if not self._heap:
            raise PriorityQueueEmptyError(
                f"peek() called on an empty CategoryPriorityQueue for category "
                f"{self.category!r}"
            )
        _, _, _, task = self._heap[0]
        return task

    def __len__(self) -> int:
        return len(self._heap)

    def is_empty(self) -> bool:
        return not self._heap
