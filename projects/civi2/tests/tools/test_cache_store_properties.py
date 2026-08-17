"""Property-based tests for CacheStore (Tasks 10.2, 10.3).

# Feature: civicpilot, Property 10: Cache entries with failure or partial status are never persisted as authoritative
# Feature: civicpilot, Property 11: Expired cache entries are never treated as hits

Validates: Requirements 12.5, 12.6, 12.7
"""

from __future__ import annotations

import asyncio
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from civicpilot.tools.cache_store import CACHE_TTL_SECONDS, CacheEntry, CacheKey, CacheStore


@st.composite
def cache_entry_with_status(draw):
    status = draw(st.sampled_from(["success", "partial", "failure"]))
    content = draw(st.text(min_size=1, max_size=100))
    timestamp = draw(st.floats(min_value=1000.0, max_value=100000.0))
    source_id = draw(st.text(min_size=1, max_size=10))
    confidence = draw(st.floats(min_value=0.0, max_value=1.0))
    return CacheEntry(
        content_payload=content,
        timestamp=timestamp,
        source_id=source_id,
        status=status,
        confidence=confidence,
    )


@given(entry=cache_entry_with_status())
@settings(max_examples=100)
def test_property_10_partial_and_failure_entries_never_persisted(entry: CacheEntry):
    # Feature: civicpilot, Property 10: Cache entries with failure or partial status are never persisted as authoritative
    store = CacheStore()
    key = CacheKey(kind="url", identifier="https://example.gov.in/test")

    async def _test():
        await store.put(key, entry)
        retrieved = await store.get(key, now=entry.timestamp + 10.0)

        if entry.status in {"partial", "failure"}:
            assert retrieved is None, f"Entry with status {entry.status} was stored when it should have been gated."
        else:
            assert retrieved is not None
            assert retrieved.status == "success"

    asyncio.run(_test())


@given(
    entry_time=st.floats(min_value=1000.0, max_value=100000.0),
    elapsed_seconds=st.floats(min_value=0.0, max_value=200000.0),
)
@settings(max_examples=100)
def test_property_11_expired_entries_never_treated_as_hits(entry_time: float, elapsed_seconds: float):
    # Feature: civicpilot, Property 11: Expired cache entries are never treated as hits
    store = CacheStore()
    key = CacheKey(kind="scheme", identifier="pm-kisan")
    entry = CacheEntry(
        content_payload="valid payload",
        timestamp=entry_time,
        source_id="src1",
        status="success",
    )

    lookup_time = entry_time + elapsed_seconds

    async def _test():
        await store.put(key, entry)
        retrieved = await store.get(key, now=lookup_time)

        if elapsed_seconds > CACHE_TTL_SECONDS:
            assert retrieved is None, f"Expired entry (elapsed={elapsed_seconds}s > {CACHE_TTL_SECONDS}s) was returned."
        else:
            assert retrieved is not None

    asyncio.run(_test())
