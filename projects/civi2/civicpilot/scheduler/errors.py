"""Typed error contract for the Scheduler's retry policy (Task 5.2).

`RecoverableToolError` is the typed exception tool executors raise to
signal "this failure is safely retryable" -- a transient network error,
temporary service unavailability, a connection reset, or a
`Recoverable_HTTP_Status` (408, 429, 500, 502, 503, 504) as defined in the
Glossary. Any other exception type raised by an executor is treated as
non-recoverable by the Scheduler (Requirement 18.8): straight to `FAILED`,
no retry.

This keeps the Scheduler itself tool-agnostic -- it has no knowledge of
Tavily, Jina, or any other specific tool's exception types -- while giving
Phase 3's real tool wrappers (Task 9+) a clear, typed contract to raise
against. A wrapper that receives, for example, an HTTP response with a
`Recoverable_HTTP_Status` code should catch that condition and re-raise it
as (or wrap it in) `RecoverableToolError` before it reaches the Scheduler.
"""

from __future__ import annotations

__all__ = ["RecoverableToolError"]


class RecoverableToolError(Exception):
    """Raised by a tool executor to signal a retryable failure.

    Covers: transient network errors, temporary service unavailability,
    connection resets, and any `Recoverable_HTTP_Status` (408, 429, 500,
    502, 503, 504). On this exception, the Scheduler retries the task
    exactly once using the same timeout as the original attempt
    (Requirement 2.6, 18.7); if the retry also fails (for any reason,
    including a second `RecoverableToolError`), the task's status becomes
    `FAILED` and no further retry is attempted (Requirement 18.9).

    Any exception type other than `RecoverableToolError` raised by an
    executor is treated as non-recoverable: the Scheduler marks the task
    `FAILED` immediately with no retry (Requirement 18.8).
    """
