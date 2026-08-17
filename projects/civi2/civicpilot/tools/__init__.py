"""Tool layer: thin proxy wrappers around external services.

Includes SearchToolProxy (Tavily), FetchToolProxy (Jina Reader), CacheStore
(ChromaDB), ModelClient (Fast_Model/Reasoning_Model), DocumentGenerator
(python-docx), and PortalValidator. Every wrapper submits work through the
Scheduler rather than invoking the underlying client directly.
"""

from civicpilot.tools.cache_store import CacheEntry, CacheKey, CacheStore
from civicpilot.tools.document_generator import DocumentGenerator
from civicpilot.tools.errors import NonRecoverableToolError, RecoverableToolError, is_recoverable_http_status
from civicpilot.tools.fetch_tool import FetchedPage, FetchToolProxy
from civicpilot.tools.model_client import ModelClient
from civicpilot.tools.portal_validator import PortalValidator
from civicpilot.tools.search_tool import RawSearchResult, SearchToolProxy

__all__ = [
    "SearchToolProxy",
    "RawSearchResult",
    "FetchToolProxy",
    "FetchedPage",
    "CacheStore",
    "CacheKey",
    "CacheEntry",
    "ModelClient",
    "DocumentGenerator",
    "PortalValidator",
    "RecoverableToolError",
    "NonRecoverableToolError",
    "is_recoverable_http_status",
]
