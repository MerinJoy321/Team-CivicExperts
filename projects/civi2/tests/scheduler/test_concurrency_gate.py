"""Unit tests for civicpilot.scheduler.concurrency_gate.

Covers per-category slot-limit enforcement, release freeing a slot back up,
and independent gating across categories (Task 4.2, Requirements 2.3, 23.5).
"""

from __future__ import annotations

import asyncio

import pytest

from civicpilot.config.settings import ConcurrencyConfig
from civicpilot.scheduler.concurrency_gate import (
    DEFAULT_LIMIT_FOR_UNSPECIFIED_CATEGORIES,
    ConcurrencyGates,
)
from civicpilot.scheduler.models import ToolCategory


def make_gates(**overrides: int) -> ConcurrencyGates:
    """Builds a ConcurrencyGates covering every ToolCategory, defaulting
    every category to a generous limit unless overridden."""
    limits = {category: 5 for category in ToolCategory}
    for category_name, limit in overrides.items():
        limits[ToolCategory(category_name)] = limit
    return ConcurrencyGates(limits)


class TestConstruction:
    def test_requires_every_category_present(self) -> None:
        incomplete = {ToolCategory.SEARCH: 4}
        with pytest.raises(ValueError):
            ConcurrencyGates(incomplete)

    def test_rejects_limit_below_one(self) -> None:
        limits = {category: 4 for category in ToolCategory}
        limits[ToolCategory.FETCH] = 0
        with pytest.raises(ValueError):
            ConcurrencyGates(limits)

    def test_from_concurrency_config_uses_configured_search_fetch_verify(self) -> None:
        config = ConcurrencyConfig(
            max_search_concurrency=2, max_fetch_concurrency=3, max_verify_concurrency=1
        )
        gates = ConcurrencyGates.from_concurrency_config(config)
        assert gates.limit_for(ToolCategory.SEARCH) == 2
        assert gates.limit_for(ToolCategory.FETCH) == 3
        assert gates.limit_for(ToolCategory.VERIFY) == 1

    def test_from_concurrency_config_defaults_unspecified_categories(self) -> None:
        config = ConcurrencyConfig(
            max_search_concurrency=4, max_fetch_concurrency=4, max_verify_concurrency=3
        )
        gates = ConcurrencyGates.from_concurrency_config(config)
        for category in (
            ToolCategory.FAST_MODEL,
            ToolCategory.REASONING_MODEL,
            ToolCategory.PORTAL_VALIDATE,
            ToolCategory.DOCUMENT,
            ToolCategory.CACHE,
        ):
            assert gates.limit_for(category) == DEFAULT_LIMIT_FOR_UNSPECIFIED_CATEGORIES

    def test_from_concurrency_config_accepts_custom_default_limit(self) -> None:
        config = ConcurrencyConfig(
            max_search_concurrency=4, max_fetch_concurrency=4, max_verify_concurrency=3
        )
        gates = ConcurrencyGates.from_concurrency_config(config, default_limit=9)
        assert gates.limit_for(ToolCategory.DOCUMENT) == 9


class TestSlotLimitEnforcement:
    @pytest.mark.asyncio
    async def test_excess_acquirers_wait_until_a_slot_frees_up(self) -> None:
        gates = make_gates(search=2)
        started = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        async def fake_work() -> None:
            nonlocal started, max_concurrent
            async with gates.acquire(ToolCategory.SEARCH):
                async with lock:
                    started += 1
                    max_concurrent = max(max_concurrent, started)
                await asyncio.sleep(0.1)
                async with lock:
                    started -= 1

        # 5 concurrent attempts against a limit of 2.
        await asyncio.gather(*(fake_work() for _ in range(5)))

        assert max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_active_count_never_exceeds_limit_under_contention(self) -> None:
        gates = make_gates(fetch=3)
        observed_max = 0
        lock = asyncio.Lock()

        async def fake_work() -> None:
            nonlocal observed_max
            async with gates.acquire(ToolCategory.FETCH):
                async with lock:
                    observed_max = max(observed_max, gates.active_count(ToolCategory.FETCH))
                await asyncio.sleep(0.05)

        await asyncio.gather(*(fake_work() for _ in range(8)))

        assert observed_max <= 3
        # All slots should be free again once every task has completed.
        assert gates.active_count(ToolCategory.FETCH) == 0
        assert gates.available_count(ToolCategory.FETCH) == 3

    @pytest.mark.asyncio
    async def test_a_blocked_acquirer_proceeds_once_a_slot_is_released(self) -> None:
        gates = make_gates(verify=1)
        order: list[str] = []

        async def holder() -> None:
            async with gates.acquire(ToolCategory.VERIFY):
                order.append("holder-start")
                await asyncio.sleep(0.15)
                order.append("holder-end")

        async def waiter() -> None:
            # Give the holder a head start so it acquires the single slot first.
            await asyncio.sleep(0.02)
            async with gates.acquire(ToolCategory.VERIFY):
                order.append("waiter-start")

        await asyncio.gather(holder(), waiter())

        assert order == ["holder-start", "holder-end", "waiter-start"]


class TestReleaseFreesSlot:
    @pytest.mark.asyncio
    async def test_release_on_normal_exit_makes_slot_available_again(self) -> None:
        gates = make_gates(search=1)

        async with gates.acquire(ToolCategory.SEARCH):
            assert gates.active_count(ToolCategory.SEARCH) == 1
            assert gates.available_count(ToolCategory.SEARCH) == 0

        assert gates.active_count(ToolCategory.SEARCH) == 0
        assert gates.available_count(ToolCategory.SEARCH) == 1

    @pytest.mark.asyncio
    async def test_release_on_exception_still_frees_the_slot(self) -> None:
        gates = make_gates(fetch=1)

        with pytest.raises(RuntimeError):
            async with gates.acquire(ToolCategory.FETCH):
                assert gates.active_count(ToolCategory.FETCH) == 1
                raise RuntimeError("boom")

        assert gates.active_count(ToolCategory.FETCH) == 0
        assert gates.available_count(ToolCategory.FETCH) == 1

    @pytest.mark.asyncio
    async def test_sequential_acquisitions_reuse_the_freed_slot(self) -> None:
        gates = make_gates(verify=1)

        async with gates.acquire(ToolCategory.VERIFY):
            pass
        async with gates.acquire(ToolCategory.VERIFY):
            pass

        assert gates.active_count(ToolCategory.VERIFY) == 0


class TestCategoriesGatedIndependently:
    @pytest.mark.asyncio
    async def test_exhausting_search_slots_does_not_block_fetch(self) -> None:
        gates = make_gates(search=1, fetch=1)

        search_started = asyncio.Event()
        fetch_completed = asyncio.Event()

        async def hold_search() -> None:
            async with gates.acquire(ToolCategory.SEARCH):
                search_started.set()
                # Hold the only SEARCH slot well past the fetch task's own runtime.
                await asyncio.sleep(0.3)

        async def do_fetch() -> None:
            await search_started.wait()
            async with gates.acquire(ToolCategory.FETCH):
                pass
            fetch_completed.set()

        search_task = asyncio.create_task(hold_search())
        await asyncio.wait_for(search_started.wait(), timeout=1.0)

        # FETCH must be able to proceed and complete while SEARCH's single
        # slot is still held.
        await asyncio.wait_for(do_fetch(), timeout=1.0)
        assert fetch_completed.is_set()

        search_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await search_task

    @pytest.mark.asyncio
    async def test_each_category_tracks_its_own_active_count(self) -> None:
        gates = make_gates(search=2, fetch=2, verify=2)

        async with gates.acquire(ToolCategory.SEARCH):
            async with gates.acquire(ToolCategory.SEARCH):
                assert gates.active_count(ToolCategory.SEARCH) == 2
                assert gates.active_count(ToolCategory.FETCH) == 0
                assert gates.active_count(ToolCategory.VERIFY) == 0

        assert gates.active_count(ToolCategory.SEARCH) == 0
