"""Monotonic-clock timing and instrumentation utilities.

These helpers back the `started_at`/`completed_at` fields on `ToolTask`
(Requirement 2.1, 2.8) and the batch-timing computations performed by the
Scheduler (Requirement 2.10) and TelemetryModule (Requirement 4.6-4.8). The
full `TelemetryModule` snapshot/recording logic is implemented in Task 6;
this module only provides the low-level clock primitives it and the
Scheduler are built on.

`time.monotonic()` is used rather than `time.time()` because it is
guaranteed not to run backwards (e.g. due to system clock adjustments or
NTP sync), which matters for correctly measuring elapsed durations and
batch/critical-path timing.
"""

from __future__ import annotations

import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

#: Number of decimal places corresponding to millisecond precision when
#: a duration is expressed in seconds (Requirement 4.6).
_MS_PRECISION_DECIMALS = 3


def now_s() -> float:
    """Returns the current monotonic-clock timestamp, in seconds.

    Suitable for `ToolTask.started_at`/`completed_at` and any other
    point-in-time measurement that will later be subtracted from another
    `now_s()` reading to compute an elapsed duration. Values returned by
    this function have no meaningful relationship to wall-clock time (they
    are not epoch timestamps) and SHOULD NOT be persisted, displayed, or
    compared across process restarts -- only differences between two
    `now_s()` readings taken within the same process are meaningful.
    """
    return time.monotonic()


def elapsed_s(start: float, end: float | None = None) -> float:
    """Returns the elapsed duration, in seconds, between `start` and `end`.

    Both `start` and `end` are expected to be `now_s()` readings taken
    within the same process. If `end` is omitted, the current time
    (`now_s()`) is used, giving the elapsed time from `start` up to now.

    Raises `ValueError` if the computed elapsed time would be negative
    (i.e. `end` occurs before `start`), since a negative duration always
    indicates a caller bug (mismatched timestamps) rather than a valid
    measurement -- monotonic-clock readings taken in the correct order
    can never produce a negative difference.
    """
    if end is None:
        end = now_s()
    duration = end - start
    if duration < 0:
        raise ValueError(
            f"elapsed_s() computed a negative duration ({duration!r}); "
            "end must not occur before start"
        )
    return duration


def format_seconds_ms(value: float) -> float:
    """Rounds a duration expressed in seconds to millisecond precision.

    Used for every Telemetry snapshot timing field (intake_time,
    planning_time, search_time, filtering_time, fetch_time,
    verification_time, synthesis_time, document_time, total_time) per
    Requirement 4.6, which requires each of those fields to be "expressed
    in seconds with millisecond precision".

    Rounding uses banker's-rounding-free `round()` behavior at 3 decimal
    places (1 second = 1000 milliseconds, so 3 decimal places of seconds
    is exactly millisecond resolution). Negative inputs are rejected since
    a duration can never be negative.
    """
    if value < 0:
        raise ValueError(f"format_seconds_ms() received a negative duration: {value!r}")
    return round(value, _MS_PRECISION_DECIMALS)


@dataclass
class Stopwatch(AbstractContextManager["Stopwatch"]):
    """A small context manager for convenient stage timing.

    Records a `start_s`/`end_s` pair (in `now_s()` units) around the
    wrapped block and exposes the elapsed duration, in seconds, rounded to
    millisecond precision, via `elapsed_ms_precision`. Intended for
    repeated use across the Scheduler and pipeline stage implementations
    in later tasks (e.g. `with Stopwatch() as sw: ...` followed by
    `sw.elapsed_ms_precision`); the full Telemetry_Module task/batch hooks
    land in Task 6 and are not duplicated here.

    Example:
        with Stopwatch() as sw:
            do_work()
        stage_time = sw.elapsed_ms_precision
    """

    start_s: float = field(default=0.0, init=False)
    end_s: float | None = field(default=None, init=False)

    def __enter__(self) -> "Stopwatch":
        self.start_s = now_s()
        self.end_s = None
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.end_s = now_s()
        return None

    @property
    def elapsed_s(self) -> float:
        """Elapsed duration in seconds, unrounded.

        While the stopwatch is still running (has not exited its `with`
        block), this returns the elapsed time so far, measured up to now.
        """
        return elapsed_s(self.start_s, self.end_s)

    @property
    def elapsed_ms_precision(self) -> float:
        """Elapsed duration in seconds, rounded to millisecond precision."""
        return format_seconds_ms(self.elapsed_s)


def timed(func: _F) -> _F:
    """Decorator that measures a sync or async function's wall-clock duration.

    The wrapped function's return value is unchanged; the measured
    duration (seconds, millisecond precision) is attached to the wrapper
    as `last_elapsed_s` after each call, for convenient inline stage
    timing without requiring a full `Stopwatch` block. This is a minimal
    convenience helper -- the full Telemetry_Module hooks that record
    per-task/per-batch timings into a `TelemetrySnapshot` are implemented
    in Task 6 and should be used instead of this decorator for anything
    that needs to be reported in telemetry.
    """
    import asyncio

    if asyncio.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = now_s()
            try:
                return await func(*args, **kwargs)
            finally:
                async_wrapper.last_elapsed_s = format_seconds_ms(elapsed_s(start))

        async_wrapper.last_elapsed_s = 0.0
        return async_wrapper  # type: ignore[return-value]

    @wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        start = now_s()
        try:
            return func(*args, **kwargs)
        finally:
            sync_wrapper.last_elapsed_s = format_seconds_ms(elapsed_s(start))

    sync_wrapper.last_elapsed_s = 0.0
    return sync_wrapper  # type: ignore[return-value]
