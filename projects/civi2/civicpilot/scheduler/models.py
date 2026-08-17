"""Scheduler task model: `ToolCategory`, `TaskStatus`, and `ToolTask`
(Requirement 2.1).

These are pure data definitions with no scheduling behavior -- dependency
graph resolution, priority/FIFO dispatch, concurrency gating, timeout/retry
policy, and cancellation are implemented on top of this model in later
tasks (3.2 onward). This module only defines the shape of a tool task and
validates the two field constraints Requirement 2.1 states explicitly:
`priority` must be a numeric value between 1 (highest) and 10 (lowest)
inclusive, and `timeout_ms` must be greater than zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

#: Requirement 2.1: priority is a numeric value between 1 (highest) and 10
#: (lowest), inclusive.
_PRIORITY_MIN = 1
_PRIORITY_MAX = 10


class ToolCategory(str, Enum):
    """The tool category a `ToolTask` belongs to.

    Concurrency limits (Requirement 2.3, 23.5) and per-category priority
    dispatch (Requirement 2.4) are both scoped per `ToolCategory`. `CACHE`
    lookups are deterministic and unscheduled (no concurrency gate, no
    priority queue) but are still counted for tool-budget telemetry
    (Requirement 17), so they get a category value like every other tool.
    """

    SEARCH = "search"
    FETCH = "fetch"
    VERIFY = "verify"
    FAST_MODEL = "fast_model"
    REASONING_MODEL = "reasoning_model"
    PORTAL_VALIDATE = "portal_validate"
    DOCUMENT = "document"
    CACHE = "cache"


class TaskStatus(str, Enum):
    """The lifecycle status of a `ToolTask` (Requirement 2.8).

    A task's terminal statuses are `COMPLETED`, `FAILED`, `TIMED_OUT`,
    `CANCELLED`, and `SKIPPED`; `PENDING`, `ELIGIBLE`, and `RUNNING` are
    non-terminal. The dependency graph (Task 3.2) transitions
    `PENDING -> ELIGIBLE` once every dependency reaches `COMPLETED`, or to
    `SKIPPED` if any dependency reaches `FAILED`, `TIMED_OUT`, or
    `CANCELLED` (Requirement 2.11).
    """

    PENDING = "pending"
    ELIGIBLE = "eligible"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


#: Terminal statuses that a dependency reaching them should cause a
#: dependent task to be `SKIPPED` rather than executed (Requirement 2.11).
#: Defined here for reuse by the dependency-graph resolution implemented in
#: Task 3.2, so that module does not need to redeclare this set.
SKIP_PROPAGATING_STATUSES = frozenset(
    {TaskStatus.FAILED, TaskStatus.TIMED_OUT, TaskStatus.CANCELLED}
)


@dataclass
class ToolTask:
    """A single unit of scheduled tool work (Requirement 2.1).

    Fields match the design's canonical `ToolTask` dataclass exactly:
    `task_id`, `category`, `tool_name`, `params`, `priority` (1-10),
    `timeout_ms` (> 0), `depends_on`, `agent_role`, `status`,
    `retries_used`, `result`, `error`, `started_at`, `completed_at`,
    `dependency_satisfied_at`.

    `priority` and `timeout_ms` are validated at construction time
    (`__post_init__`); an out-of-range `priority` or non-positive
    `timeout_ms` raises `ValueError` immediately rather than allowing an
    invalid task to be submitted to the Scheduler.
    """

    task_id: str
    category: ToolCategory
    tool_name: str
    params: dict[str, Any]
    priority: int  # 1 (highest) .. 10 (lowest), inclusive
    timeout_ms: int  # > 0
    depends_on: list[str]
    agent_role: str
    status: TaskStatus = TaskStatus.PENDING
    retries_used: int = 0
    result: Any = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    dependency_satisfied_at: Optional[float] = None

    def __post_init__(self) -> None:
        if not (_PRIORITY_MIN <= self.priority <= _PRIORITY_MAX):
            raise ValueError(
                f"priority must be between {_PRIORITY_MIN} and {_PRIORITY_MAX} "
                f"inclusive, got {self.priority!r}"
            )
        if self.timeout_ms <= 0:
            raise ValueError(
                f"timeout_ms must be greater than zero, got {self.timeout_ms!r}"
            )
