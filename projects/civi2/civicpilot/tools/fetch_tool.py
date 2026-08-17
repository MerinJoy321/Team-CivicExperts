"""FetchToolProxy (Jina Reader wrapper) (Requirements 11.1, 11.3-11.5, 12.1, 18.2).

Extracts markdown content from URLs using Jina Reader (or configured fetch engine).
Checks Cache_Store for unexpired URL entries before submitting tasks to the Scheduler.
On miss, submits a FETCH task with a timeout in the 8-10s range (default 9.0s / 9000ms).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

from civicpilot.scheduler.models import ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.telemetry.timing import now_s
from civicpilot.tools.cache_store import CacheEntry, CacheKey, CacheStore
from civicpilot.tools.errors import NonRecoverableToolError, RecoverableToolError, is_recoverable_http_status

DEFAULT_FETCH_TIMEOUT_MS = 9000  # 9 seconds (8-10s range per Requirement 18.2)


@dataclass(frozen=True)
class FetchedPage:
    """Represents page content retrieved by FetchToolProxy."""

    url: str
    content: str
    title: str = ""
    cached: bool = False
    status: str = "success"  # "success" | "partial" | "failure"


class FetchToolProxy:
    """Proxy for URL page extraction operations.

    Checks CacheStore first; on miss, submits a FETCH task to Scheduler.
    """

    def __init__(
        self,
        scheduler: Scheduler,
        cache_store: Optional[CacheStore] = None,
        fetch_client: Optional[Any] = None,
        timeout_ms: int = DEFAULT_FETCH_TIMEOUT_MS,
    ) -> None:
        self._scheduler = scheduler
        self._cache_store = cache_store
        self._fetch_client = fetch_client
        self._timeout_ms = timeout_ms

    async def fetch(
        self,
        url: str,
        *,
        priority: int = 5,
        depends_on: Optional[list[str]] = None,
        agent_role: str = "Researcher_Agent",
        task_id: Optional[str] = None,
    ) -> Union[FetchedPage, ToolTask]:
        """Fetches page content for `url`.

        If present and unexpired in `CacheStore`, returns a `FetchedPage(cached=True)` directly.
        Otherwise, submits a `ToolTask` to Scheduler under `ToolCategory.FETCH`.
        """
        key = CacheKey(kind="url", identifier=url)

        # Check CacheStore first
        if self._cache_store is not None:
            cached_entry = await self._cache_store.get(key)
            if cached_entry is not None:
                payload = cached_entry.content_payload
                if isinstance(payload, dict):
                    content = payload.get("content", "")
                    title = payload.get("title", "")
                else:
                    content = str(payload)
                    title = ""
                return FetchedPage(
                    url=url,
                    content=content,
                    title=title,
                    cached=True,
                    status=cached_entry.status,
                )

        tid = task_id or f"fetch_{id(url)}"
        task = ToolTask(
            task_id=tid,
            category=ToolCategory.FETCH,
            tool_name="jina_fetch",
            params={"url": url},
            priority=priority,
            timeout_ms=self._timeout_ms,
            depends_on=depends_on or [],
            agent_role=agent_role,
        )

        return await self._scheduler.submit(task)
