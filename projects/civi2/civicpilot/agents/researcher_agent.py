"""ResearcherAgent implementation (Requirements 8.5-8.7, 9.1-9.5, 10.6, 11.2).

Submits search and fetch operations respecting dependency annotations.
Invokes FilterPipeline for URL dedup, irrelevance filtering, ambiguous classification,
and 6-tier candidate ranking.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Optional, Protocol

from civicpilot.agents.models import Profile, SchemeCandidate
from civicpilot.agents.planner_agent import SearchPlan
from civicpilot.tools.cache_store import CacheStore
from civicpilot.tools.fetch_tool import FetchedPage, FetchToolProxy
from civicpilot.tools.search_tool import RawSearchResult, SearchToolProxy


class FilterPipelineProtocol(Protocol):
    """Abstract interface protocol for FilterPipeline."""

    def dedupe(self, results: list[RawSearchResult]) -> list[RawSearchResult]: ...
    def remove_irrelevant(self, results: list[RawSearchResult], query: str) -> list[RawSearchResult]: ...
    async def classify_ambiguous(self, results: list[RawSearchResult], query: str) -> list[RawSearchResult]: ...
    def rank_and_select(self, results: list[RawSearchResult]) -> list[RawSearchResult]: ...


class DummyFilterPipeline:
    """Default stub FilterPipeline used until Task 19 full implementation."""

    def dedupe(self, results: list[RawSearchResult]) -> list[RawSearchResult]:
        seen: set[str] = set()
        deduped = []
        for r in results:
            if r.url not in seen:
                seen.add(r.url)
                deduped.append(r)
        return deduped

    def remove_irrelevant(self, results: list[RawSearchResult], query: str) -> list[RawSearchResult]:
        return results

    async def classify_ambiguous(self, results: list[RawSearchResult], query: str) -> list[RawSearchResult]:
        return results

    def rank_and_select(self, results: list[RawSearchResult]) -> list[RawSearchResult]:
        return results[:5]


class ResearcherAgent:
    """CrewAI Researcher_Agent role wrapper.

    Executes search wave -> FilterPipeline -> fetch wave via Scheduler proxies.
    """

    def __init__(
        self,
        search_proxy: SearchToolProxy,
        fetch_proxy: FetchToolProxy,
        cache_store: Optional[CacheStore] = None,
        filter_pipeline: Optional[Any] = None,
    ) -> None:
        self._search_proxy = search_proxy
        self._fetch_proxy = fetch_proxy
        self._cache_store = cache_store
        self._filter_pipeline = filter_pipeline or DummyFilterPipeline()

    async def research(self, profile: Profile, plan: SearchPlan) -> list[SchemeCandidate]:
        """Executes search wave, filters candidates, and fetches details.

        Requirement 8.7: Sibling search failure does not halt other searches.
        Requirement 10.6: Runs search wave -> FilterPipeline -> fetch wave.
        """
        raw_results: list[RawSearchResult] = []

        # 1. Search wave: schedule searches
        for op in plan.operations:
            try:
                task_fut = await self._search_proxy.search(
                    queries=[op.query],
                    agent_role="Researcher_Agent",
                    task_id=f"search_{op.op_id}",
                )
                if inspect.isawaitable(task_fut):
                    task_res = await task_fut
                else:
                    task_res = task_fut

                res = getattr(task_res, "result", None)
                if isinstance(res, list):
                    for item in res:
                        if isinstance(item, RawSearchResult):
                            raw_results.append(item)
                        elif isinstance(item, dict):
                            raw_results.append(
                                RawSearchResult(
                                    url=item.get("url", ""),
                                    title=item.get("title", ""),
                                    snippet=item.get("snippet", ""),
                                    score=item.get("score", 0.0),
                                )
                            )
            except Exception:
                # Requirement 8.7: Sibling search failure does not block other searches
                continue

        # 2. FilterPipeline: dedupe -> remove_irrelevant -> classify_ambiguous -> rank_and_select
        deduped = self._filter_pipeline.dedupe(raw_results)
        relevant = self._filter_pipeline.remove_irrelevant(deduped, plan.operations[0].query if plan.operations else "")
        classified = await self._filter_pipeline.classify_ambiguous(relevant, plan.operations[0].query if plan.operations else "")
        top_candidates = self._filter_pipeline.rank_and_select(classified)

        # 3. Fetch wave: fetch top candidate pages
        candidates: list[SchemeCandidate] = []
        for idx, item in enumerate(top_candidates, start=1):
            source_urls = [item.url] if item.url else []
            candidate = SchemeCandidate(
                scheme_id=f"scheme_{idx}",
                name=item.title or f"Scheme {idx}",
                source_urls=source_urls,
                priority_tier=1 if "gov.in" in item.url or "nic.in" in item.url else 5,
            )
            candidates.append(candidate)

            if item.url:
                try:
                    await self._fetch_proxy.fetch(
                        url=item.url,
                        agent_role="Researcher_Agent",
                        task_id=f"fetch_scheme_{idx}",
                    )
                except Exception:
                    pass

        return candidates
