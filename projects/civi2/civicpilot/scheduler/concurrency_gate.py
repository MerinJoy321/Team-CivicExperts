"""Per-`ToolCategory` concurrency gates (Requirements 2.3, 23.5).

This module implements the piece of Scheduler behavior the design calls
"Concurrency gates": *one `asyncio.Semaphore` per `ToolCategory`, sized from
the Task 1.2 config loader (`MAX_SEARCH_CONCURRENCY`, `MAX_FETCH_CONCURRENCY`,
`MAX_VERIFY_CONCURRENCY`)*. Per the design's "Design notes mapped to
acceptance criteria" section:

    A task only leaves the queue when it both is ELIGIBLE and a semaphore
    slot is available; this is the sole gate on concurrent execution, so
    the limit can never be structurally exceeded.

`ConcurrencyGates` is deliberately independent of the priority heap (Task
4.1) and the dependency graph (Task 3.2) -- it only knows how to bound and
report *how many concurrent slots per category are in use*. The eventual
`Scheduler.run()` loop (Task 4.3) is expected to check a task's category
against `ConcurrencyGates.acquire(category)` only once that task is already
`ELIGIBLE` and has won its turn in the priority heap.

Category limit resolution
--------------------------
Requirement 23 (and Requirement 2.3) only specify configured limits for the
``SEARCH``, ``FETCH``, and ``VERIFY`` categories via
``civicpilot.config.settings.ConcurrencyConfig``. The other `ToolCategory`
members (`FAST_MODEL`, `REASONING_MODEL`, `PORTAL_VALIDATE`, `DOCUMENT`,
`CACHE`) have no requirement-specified env var or bound. Later tasks (11.x
model wrappers, 12.x document/portal wrappers) still need to submit tasks in
those categories and therefore still need *some* gate, so this module
assigns them a generous fixed default (`DEFAULT_LIMIT_FOR_UNSPECIFIED_CATEGORIES
= 5`) rather than leaving them unbounded. That choice is documented here
(and repeated at the constant's definition) precisely because it is a design
decision this task had to make, not one specified by a requirement.

Construction: dict-first, with a config-loader convenience
------------------------------------------------------------
The primary constructor (`ConcurrencyGates.__init__`) accepts a plain
``dict[ToolCategory, int]`` covering every `ToolCategory` member -- this is
the most flexible shape and imposes no dependency on
`civicpilot.config.settings`. `ConcurrencyGates.from_concurrency_config` is a
convenience classmethod that builds that mapping from a
`ConcurrencyConfig` (as produced by `civicpilot.config.settings.load_settings
().concurrency`), filling in the unspecified categories with the default
above, so the eventual `Scheduler` class (Task 4.3) can construct this
directly from a loaded `Settings` object with one call:
``ConcurrencyGates.from_concurrency_config(settings.concurrency)``.

Acquire/release API shape
--------------------------
`acquire()` is an async context manager (usable as
``async with gates.acquire(ToolCategory.SEARCH): ...``) rather than separate
``acquire()``/``release()`` methods. This is chosen because it makes the
slot's release exception-safe: any error raised inside the ``async with``
block still releases the slot, whereas paired ``acquire()``/``release()``
calls would require every call site to wrap the guarded work in its own
``try/finally`` to get the same guarantee.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Mapping

import asyncio

from civicpilot.scheduler.models import ToolCategory

if TYPE_CHECKING:
    from civicpilot.config.settings import ConcurrencyConfig

__all__ = ["ConcurrencyGates", "DEFAULT_LIMIT_FOR_UNSPECIFIED_CATEGORIES"]

#: Generous fixed default concurrency limit for `ToolCategory` members that
#: Requirement 23 does not assign a configured env var to (`FAST_MODEL`,
#: `REASONING_MODEL`, `PORTAL_VALIDATE`, `DOCUMENT`, `CACHE`). See the
#: module docstring's "Category limit resolution" section for rationale.
DEFAULT_LIMIT_FOR_UNSPECIFIED_CATEGORIES = 5


class _CategorySlot:
    """Bookkeeping for a single `ToolCategory`'s semaphore and active count.

    `active` is only ever mutated between the `await semaphore.acquire()`
    and `semaphore.release()` calls in `ConcurrencyGates.acquire`, and both
    mutations are plain (non-awaiting) integer increments/decrements, so no
    additional lock is needed for correctness under asyncio's single-threaded
    cooperative scheduling.
    """

    __slots__ = ("limit", "semaphore", "active")

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.semaphore = asyncio.Semaphore(limit)
        self.active = 0


class ConcurrencyGates:
    """Holds one `asyncio.Semaphore` per `ToolCategory`, sized per category.

    A task's category "leaves the queue" (in the design's terms) only once
    both it is `ELIGIBLE` (Task 3.2 / dependency graph) and a slot is
    available here -- `ConcurrencyGates` enforces the latter half of that
    condition and is the *sole* gate on concurrent execution: it never
    imposes any other constraint (priority, dependency state, retries) on
    when a slot may be acquired.
    """

    def __init__(self, limits: Mapping[ToolCategory, int]) -> None:
        """Builds one semaphore per `ToolCategory` member.

        Args:
            limits: A mapping covering every `ToolCategory` member to its
                concurrency limit (an integer >= 1). Use
                `from_concurrency_config` for the common case of deriving
                `SEARCH`/`FETCH`/`VERIFY` limits from a loaded
                `ConcurrencyConfig`.

        Raises:
            ValueError: if any `ToolCategory` member is missing from
                `limits`, or if any supplied limit is less than 1
                (Requirement 2.3: "an integer greater than or equal to 1").
        """
        missing = [category for category in ToolCategory if category not in limits]
        if missing:
            raise ValueError(
                "limits mapping is missing an entry for: "
                + ", ".join(category.value for category in missing)
            )
        invalid = {
            category: limit for category, limit in limits.items() if limit < 1
        }
        if invalid:
            raise ValueError(
                "concurrency limit must be an integer >= 1, got: "
                + ", ".join(f"{category.value}={limit}" for category, limit in invalid.items())
            )

        self._slots: dict[ToolCategory, _CategorySlot] = {
            category: _CategorySlot(limits[category]) for category in ToolCategory
        }

    @classmethod
    def from_concurrency_config(
        cls,
        concurrency: "ConcurrencyConfig",
        *,
        default_limit: int = DEFAULT_LIMIT_FOR_UNSPECIFIED_CATEGORIES,
    ) -> "ConcurrencyGates":
        """Builds `ConcurrencyGates` from a loaded `ConcurrencyConfig`.

        `SEARCH`, `FETCH`, and `VERIFY` use the configured
        `max_search_concurrency` / `max_fetch_concurrency` /
        `max_verify_concurrency` values (Requirements 2.3, 23.5). Every
        other `ToolCategory` member uses `default_limit` (see the module
        docstring's "Category limit resolution" section).
        """
        limits: dict[ToolCategory, int] = {
            ToolCategory.SEARCH: concurrency.max_search_concurrency,
            ToolCategory.FETCH: concurrency.max_fetch_concurrency,
            ToolCategory.VERIFY: concurrency.max_verify_concurrency,
        }
        for category in ToolCategory:
            limits.setdefault(category, default_limit)
        return cls(limits)

    # ------------------------------------------------------------------
    # Acquire / release
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def acquire(self, category: ToolCategory) -> AsyncIterator[None]:
        """Acquires one concurrency slot for `category`, as an async CM.

        Usage::

            async with gates.acquire(ToolCategory.SEARCH):
                ...  # execute the task; slot is held for the block's duration

        Blocks (awaits) until a slot is available whenever `category` is
        already at its configured limit; releases the slot on normal exit
        *and* on any exception raised inside the block.
        """
        slot = self._slots[category]
        await slot.semaphore.acquire()
        slot.active += 1
        try:
            yield
        finally:
            slot.active -= 1
            slot.semaphore.release()

    # ------------------------------------------------------------------
    # Inspection (for Task 6 Telemetry hooks: maximum_concurrency reporting)
    # ------------------------------------------------------------------

    def limit_for(self, category: ToolCategory) -> int:
        """Returns the configured concurrency limit for `category`."""
        return self._slots[category].limit

    def active_count(self, category: ToolCategory) -> int:
        """Returns the number of slots currently held (in-use) for `category`."""
        return self._slots[category].active

    def available_count(self, category: ToolCategory) -> int:
        """Returns the number of free slots currently available for `category`."""
        return self.limit_for(category) - self.active_count(category)
