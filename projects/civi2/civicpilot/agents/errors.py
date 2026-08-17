"""Typed exceptions for the CrewAI agent layer (Requirements 1.5, 8.3)."""

from __future__ import annotations

__all__ = ["PlanningFailure", "ArchitectureViolationError"]


class PlanningFailure(Exception):
    """Raised when PlannerAgent planning call fails, times out, or produces an
    invalid search plan (fewer than 3 or more than 5 operations, or duplicate queries)
    per Requirement 8.3.
    """


class ArchitectureViolationError(Exception):
    """Raised by SchedulerToolProxy when an agent or code path attempts to invoke
    a tool outside a registered agent context or bypassing the Scheduler
    per Requirement 1.5.
    """
