"""SchedulerToolProxy and architecture guard enforcement (Requirements 1.1, 1.2, 1.4, 1.5).

Sole code path for CrewAI agents to reach tools (Search, Fetch, Cache, ModelClient,
DocumentGenerator). Rejects invocations outside registered agent context with
ArchitectureViolationError, leaving prior completed state untouched.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Generator, Optional

from civicpilot.agents.errors import ArchitectureViolationError
from civicpilot.scheduler.models import ToolTask
from civicpilot.scheduler.scheduler import Scheduler

VALID_AGENT_ROLES = frozenset(
    {
        "Planner_Agent",
        "Researcher_Agent",
        "Verifier_Agent",
        "Document_Advisor_Agent",
        "Reporter_Agent",
        "Intake_Module",
    }
)

_CURRENT_AGENT_ROLE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_CURRENT_AGENT_ROLE", default=None
)


def get_current_agent_role() -> Optional[str]:
    """Returns the currently active agent role context."""
    return _CURRENT_AGENT_ROLE.get()


@contextmanager
def active_agent_context(role: str) -> Generator[None, None, None]:
    """Context manager for setting the active agent role context.

    Requirement 1.5: Tool invocations inside this context succeed;
    invocations outside raise ArchitectureViolationError.
    """
    if role not in VALID_AGENT_ROLES:
        raise ArchitectureViolationError(f"Invalid agent role context: {role!r}")

    token = _CURRENT_AGENT_ROLE.set(role)
    try:
        yield
    finally:
        _CURRENT_AGENT_ROLE.reset(token)


class SchedulerToolProxy:
    """Central Architecture Guard proxy enforcing tool routing through Scheduler.

    Requirement 1.5 / Property: Any tool invocation outside registered agent context
    is rejected before execution without altering prior state.
    """

    def __init__(self, scheduler: Scheduler) -> None:
        self._scheduler = scheduler

    async def invoke(self, task: ToolTask) -> Any:
        """Submits `task` to Scheduler after verifying agent role context.

        Raises `ArchitectureViolationError` if called outside an active agent context.
        """
        current_role = get_current_agent_role()

        if current_role is None:
            raise ArchitectureViolationError(
                "Direct tool invocation bypassing SchedulerToolProxy or active agent context is forbidden "
                f"(task_id={task.task_id!r}, tool={task.tool_name!r})."
            )

        if current_role not in VALID_AGENT_ROLES:
            raise ArchitectureViolationError(
                f"Unrecognized agent role context {current_role!r} for task {task.task_id!r}."
            )

        # Attribute task to active role
        task.agent_role = current_role

        return await self._scheduler.submit(task)
