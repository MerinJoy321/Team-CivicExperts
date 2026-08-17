"""Cache_Store (ChromaDB persistence/cache layer) (Requirements 12.1-12.7).

Provides caching for URL content, scheme records, and profile/category lookups.
Cache entries expire after 24 hours (86,400s). Entries with status in `{"partial", "failure"}`
are never persisted as authoritative.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional, Union

from civicpilot.telemetry.timing import now_s

CACHE_TTL_SECONDS = 86400.0  # 24 hours per Requirement 12.6, 12.7


class CachePersistenceError(Exception):
    """Raised when an invalid or non-authoritative entry is rejected from persistence."""


@dataclass(frozen=True)
class CacheKey:
    """Composite cache key identifying a URL, scheme, or profile category."""

    kind: str  # "url" | "scheme" | "profile_category"
    identifier: str

    @property
    def composite_id(self) -> str:
        return f"{self.kind}:{self.identifier}"


@dataclass
class CacheEntry:
    """Payload stored in CacheStore."""

    content_payload: Union[str, dict[str, Any]]
    timestamp: float
    source_id: str
    status: str = "success"  # "success" | "partial" | "failure"
    confidence: float = 1.0

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        now = current_time if current_time is not None else now_s()
        return (now - self.timestamp) > CACHE_TTL_SECONDS


class CacheStore:
    """ChromaDB-backed (or in-memory fallback) cache layer."""

    def __init__(
        self,
        chroma_client: Optional[Any] = None,
        collection_name: str = "civicpilot_cache",
    ) -> None:
        self._chroma_client = chroma_client
        self._collection_name = collection_name
        self._in_memory_store: dict[str, CacheEntry] = {}

        if self._chroma_client is not None:
            try:
                self._collection = self._chroma_client.get_or_create_collection(
                    name=collection_name
                )
            except Exception:
                self._collection = None
        else:
            self._collection = None

    async def get(self, key: CacheKey, *, now: Optional[float] = None) -> Optional[CacheEntry]:
        """Looks up entry by key. Returns None if absent or expired (>24h)."""
        cid = key.composite_id
        entry: Optional[CacheEntry] = None

        if self._collection is not None:
            try:
                result = self._collection.get(ids=[cid])
                if result and result.get("ids") and len(result["ids"]) > 0:
                    doc = result["documents"][0]
                    meta = result["metadatas"][0]
                    payload = json.loads(doc) if isinstance(doc, str) else doc
                    entry = CacheEntry(
                        content_payload=payload,
                        timestamp=float(meta.get("timestamp", 0.0)),
                        source_id=str(meta.get("source_id", "")),
                        status=str(meta.get("status", "success")),
                        confidence=float(meta.get("confidence", 1.0)),
                    )
            except Exception:
                entry = self._in_memory_store.get(cid)
        else:
            entry = self._in_memory_store.get(cid)

        if entry is None:
            return None

        if entry.is_expired(now):
            return None

        return entry

    async def put(self, key: CacheKey, entry: CacheEntry) -> None:
        """Stores entry in cache.

        Requirement 12.5 / Property 10: Entries with status in `{"partial", "failure"}`
        are never persisted as authoritative.
        """
        if entry.status in {"partial", "failure"}:
            # Gated: do not save non-authoritative entries.
            return

        cid = key.composite_id
        self._in_memory_store[cid] = entry

        if self._collection is not None:
            try:
                doc_str = (
                    json.dumps(entry.content_payload)
                    if isinstance(entry.content_payload, (dict, list))
                    else str(entry.content_payload)
                )
                metadata = {
                    "timestamp": entry.timestamp,
                    "source_id": entry.source_id,
                    "status": entry.status,
                    "confidence": entry.confidence,
                    "key_kind": key.kind,
                }
                self._collection.upsert(
                    ids=[cid],
                    documents=[doc_str],
                    metadatas=[metadata],
                )
            except Exception:
                pass
