"""SearchToolProxy (Tavily wrapper) (Requirements 9.2, 18.1).

Wraps web search (Tavily). Every issued query set automatically includes at least
one `site:myscheme.gov.in`-scoped query if not already present. Submits via the
Scheduler with category SEARCH and timeout within 6-8s (default 7.0s / 7000ms).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlparse

from civicpilot.scheduler.models import ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.tools.errors import NonRecoverableToolError, RecoverableToolError, is_recoverable_http_status

DEFAULT_SEARCH_TIMEOUT_MS = 7000  # 7 seconds (6-8s range per Requirement 18.1)


@dataclass(frozen=True)
class RawSearchResult:
    """A raw search result candidate returned by SearchToolProxy."""

    url: str
    title: str
    snippet: str
    score: float = 0.0
    domain: str = ""

    def __post_init__(self) -> None:
        if not self.domain and self.url:
            parsed = urlparse(self.url)
            object.__setattr__(self, "domain", parsed.netloc.lower())


class SearchToolProxy:
    """Proxy for web search operations (Tavily API wrapper).

    Routes search calls through the async Scheduler as `ToolTask`s under
    `ToolCategory.SEARCH`. Enforces government domain scoping and handles retryable
    vs non-retryable error classification.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        tavily_client: Optional[Any] = None,
        timeout_ms: int = DEFAULT_SEARCH_TIMEOUT_MS,
    ) -> None:
        self._scheduler = scheduler
        self._tavily_client = tavily_client
        self._timeout_ms = timeout_ms

    def _ensure_government_scoped_query(self, queries: list[str]) -> list[str]:
        """Requirement 9.2: Ensures at least one query in the list contains
        `site:myscheme.gov.in`.
        """
        if not queries:
            return ["site:myscheme.gov.in"]

        has_gov_scope = any("site:myscheme.gov.in" in q for q in queries)
        if has_gov_scope:
            return list(queries)

        primary_query = queries[0]
        scoped_query = f"{primary_query} site:myscheme.gov.in"
        return list(queries) + [scoped_query]

    async def search(
        self,
        queries: list[str],
        *,
        priority: int = 5,
        depends_on: Optional[list[str]] = None,
        agent_role: str = "Researcher_Agent",
        task_id: Optional[str] = None,
    ) -> ToolTask:
        """Submits a search task to the Scheduler.

        Returns the submitted `ToolTask` (or future).
        """
        scoped_queries = self._ensure_government_scoped_query(queries)
        tid = task_id or f"search_{id(queries)}"

        task = ToolTask(
            task_id=tid,
            category=ToolCategory.SEARCH,
            tool_name="tavily_search",
            params={"queries": scoped_queries},
            priority=priority,
            timeout_ms=self._timeout_ms,
            depends_on=depends_on or [],
            agent_role=agent_role,
        )

        return await self._scheduler.submit(task)
