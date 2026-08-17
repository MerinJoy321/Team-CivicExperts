"""Unit tests for SearchToolProxy and FetchToolProxy (Task 9.3).

Covers:
- `SearchToolProxy` automatically appending `site:myscheme.gov.in` scope (Requirement 9.2).
- Timeout enforcement in configured 6-8s and 8-10s ranges (Requirements 18.1, 18.2).
- `FetchToolProxy` checking `CacheStore` on cache hit vs submitting to Scheduler on miss (Requirement 12.1).
- Error classification for recoverable vs non-recoverable failures (Requirements 18.6-18.9).
"""

from __future__ import annotations

import asyncio
import pytest

from civicpilot.scheduler.models import ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.tools.cache_store import CacheEntry, CacheKey, CacheStore, now_s
from civicpilot.tools.errors import NonRecoverableToolError, RecoverableToolError, is_recoverable_http_status
from civicpilot.tools.fetch_tool import DEFAULT_FETCH_TIMEOUT_MS, FetchedPage, FetchToolProxy
from civicpilot.tools.search_tool import DEFAULT_SEARCH_TIMEOUT_MS, RawSearchResult, SearchToolProxy


async def _dummy_executor(task: ToolTask) -> str:
    return "ok"


@pytest.fixture
def scheduler() -> Scheduler:
    return Scheduler(
        concurrency_limits={ToolCategory.SEARCH: 4, ToolCategory.FETCH: 4},
        executor=_dummy_executor,
    )


class TestSearchToolProxy:
    @pytest.mark.asyncio
    async def test_search_ensures_government_scoped_query(self, scheduler: Scheduler) -> None:
        proxy = SearchToolProxy(scheduler)
        run_task = asyncio.create_task(scheduler.run())
        try:
            future = await proxy.search(["PM Kisan eligibility"])
            finished_task = await asyncio.wait_for(future, timeout=2.0)
            assert isinstance(finished_task, ToolTask)
            assert "queries" in finished_task.params
            queries = finished_task.params["queries"]
            assert len(queries) == 2
            assert queries[0] == "PM Kisan eligibility"
            assert "site:myscheme.gov.in" in queries[1]
        finally:
            scheduler.stop()
            await run_task

    @pytest.mark.asyncio
    async def test_search_preserves_existing_government_scope(self, scheduler: Scheduler) -> None:
        proxy = SearchToolProxy(scheduler)
        run_task = asyncio.create_task(scheduler.run())
        try:
            future = await proxy.search(["PM Kisan site:myscheme.gov.in"])
            finished_task = await asyncio.wait_for(future, timeout=2.0)
            assert isinstance(finished_task, ToolTask)
            queries = finished_task.params["queries"]
            assert len(queries) == 1
            assert queries[0] == "PM Kisan site:myscheme.gov.in"
        finally:
            scheduler.stop()
            await run_task

    def test_search_default_timeout(self, scheduler: Scheduler) -> None:
        proxy = SearchToolProxy(scheduler)
        assert 6000 <= proxy._timeout_ms <= 8000


class TestFetchToolProxy:
    @pytest.mark.asyncio
    async def test_fetch_cache_hit_returns_fetched_page_directly(self, scheduler: Scheduler) -> None:
        cache = CacheStore()
        url = "https://myscheme.gov.in/schemes/pmkisan"
        key = CacheKey(kind="url", identifier=url)
        entry = CacheEntry(
            content_payload={"content": "PM Kisan details", "title": "PM Kisan"},
            timestamp=now_s(),
            source_id="task_1",
            status="success",
        )
        await cache.put(key, entry)

        proxy = FetchToolProxy(scheduler, cache_store=cache)
        result = await proxy.fetch(url)

        assert isinstance(result, FetchedPage)
        assert result.cached is True
        assert result.content == "PM Kisan details"

    @pytest.mark.asyncio
    async def test_fetch_cache_miss_submits_scheduler_task(self, scheduler: Scheduler) -> None:
        cache = CacheStore()
        url = "https://myscheme.gov.in/schemes/unknown"
        proxy = FetchToolProxy(scheduler, cache_store=cache)
        run_task = asyncio.create_task(scheduler.run())
        try:
            future = await proxy.fetch(url)
            finished_task = await asyncio.wait_for(future, timeout=2.0)
            assert isinstance(finished_task, ToolTask)
            assert finished_task.category == ToolCategory.FETCH
            assert finished_task.params["url"] == url
        finally:
            scheduler.stop()
            await run_task

    def test_fetch_default_timeout(self, scheduler: Scheduler) -> None:
        proxy = FetchToolProxy(scheduler)
        assert 8000 <= proxy._timeout_ms <= 10000


class TestErrorClassification:
    def test_recoverable_http_statuses(self) -> None:
        recoverable_codes = [408, 429, 500, 502, 503, 504]
        for code in recoverable_codes:
            assert is_recoverable_http_status(code) is True

    def test_non_recoverable_http_statuses(self) -> None:
        non_recoverable_codes = [400, 401, 403, 404, 422]
        for code in non_recoverable_codes:
            assert is_recoverable_http_status(code) is False
