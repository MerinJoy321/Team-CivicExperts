"""Edge-case, error, timeout, and cache test suite (Requirements 11.1, 18.1, 18.6, 19.3).

Covers:
- Timeout range bounds across call categories (Requirements 18.1-18.5).
- HTTP error classification: recoverable vs non-recoverable (Requirement 18.6).
- CacheStore hit/miss behavior across composite key kinds (Requirement 11.1).
- All-fetches-failed fallback with degraded confidence (Requirement 19.3).
"""

from __future__ import annotations

import asyncio
import pytest

from civicpilot.agents.models import EligibilityResult
from civicpilot.pipeline.confidence_degradation import apply_confidence_degradation
from civicpilot.scheduler.models import ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.tools.cache_store import CacheEntry, CacheKey, CacheStore, now_s
from civicpilot.tools.errors import NonRecoverableToolError, RecoverableToolError, is_recoverable_http_status


class TestTimeoutRanges:
    def test_category_timeout_defaults(self) -> None:
        """Requirements 18.1-18.5: Timeout range validation per tool category."""
        from civicpilot.tools.fetch_tool import DEFAULT_FETCH_TIMEOUT_MS
        from civicpilot.tools.model_client import DEFAULT_FAST_MODEL_TIMEOUT_MS, DEFAULT_REASONING_MODEL_TIMEOUT_MS
        from civicpilot.tools.search_tool import DEFAULT_SEARCH_TIMEOUT_MS

        assert 2000 <= DEFAULT_FAST_MODEL_TIMEOUT_MS <= 4000
        assert 15000 <= DEFAULT_REASONING_MODEL_TIMEOUT_MS <= 30000
        assert 5000 <= DEFAULT_SEARCH_TIMEOUT_MS <= 7000
        assert 7000 <= DEFAULT_FETCH_TIMEOUT_MS <= 9000


class TestHTTPErrorClassification:
    def test_recoverable_http_statuses(self) -> None:
        """Requirement 18.6: 408, 429, 500, 502, 503, 504 are recoverable."""
        recoverable_codes = {408, 429, 500, 502, 503, 504}
        non_recoverable_codes = {400, 401, 403, 404, 405, 422}

        for code in recoverable_codes:
            assert is_recoverable_http_status(code) is True

        for code in non_recoverable_codes:
            assert is_recoverable_http_status(code) is False


class TestCacheStoreCompositeKeys:
    @pytest.mark.asyncio
    async def test_cache_hits_and_misses_across_kinds(self) -> None:
        """Requirement 11.1: CacheStore supports url, scheme, profile_category composite keys."""
        cache = CacheStore()

        k_url = CacheKey(kind="url", identifier="https://myscheme.gov.in")
        k_scheme = CacheKey(kind="scheme", identifier="PM_KISAN")
        k_cat = CacheKey(kind="profile_category", identifier="SC_ST")

        # Initial miss
        assert await cache.get(k_url) is None
        assert await cache.get(k_scheme) is None
        assert await cache.get(k_cat) is None

        # Put entries
        current_time = now_s()
        await cache.put(k_url, CacheEntry(content_payload="url_data", timestamp=current_time, source_id="s1", status="success", confidence=1.0))
        await cache.put(k_scheme, CacheEntry(content_payload="scheme_data", timestamp=current_time, source_id="s2", status="success", confidence=1.0))
        await cache.put(k_cat, CacheEntry(content_payload="cat_data", timestamp=current_time, source_id="s3", status="success", confidence=1.0))

        # Hit
        res_url = await cache.get(k_url)
        res_scheme = await cache.get(k_scheme)
        res_cat = await cache.get(k_cat)

        assert res_url is not None and res_url.content_payload == "url_data"
        assert res_scheme is not None and res_scheme.content_payload == "scheme_data"
        assert res_cat is not None and res_cat.content_payload == "cat_data"


class TestAllFetchesFailedFallback:
    def test_confidence_degraded_when_fetches_fail(self) -> None:
        """Requirement 19.3: All fetches failing leaves candidates intact with degraded confidence."""
        res = EligibilityResult(
            scheme_id="scheme_1",
            overall="POSSIBLE_NEEDS_INFO",
            confidence_level="HIGH",
        )

        degraded_res = apply_confidence_degradation(res, has_tool_failures=True)

        assert degraded_res.degraded is True
        assert degraded_res.confidence_level == "MEDIUM"
