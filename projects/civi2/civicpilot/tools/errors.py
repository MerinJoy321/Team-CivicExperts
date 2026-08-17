"""Tool layer error definitions and HTTP status classification (Requirement 18.6-18.9)."""

from __future__ import annotations

from civicpilot.scheduler.errors import RecoverableToolError

__all__ = [
    "RecoverableToolError",
    "NonRecoverableToolError",
    "RECOVERABLE_HTTP_STATUSES",
    "is_recoverable_http_status",
]

#: Glossary: Recoverable_HTTP_Status = 408, 429, 500, 502, 503, 504
RECOVERABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class NonRecoverableToolError(Exception):
    """Raised by tool wrappers when an unrecoverable failure occurs
    (e.g., 401 Unauthorized, 403 Forbidden, 404 Not Found, invalid URL, malformed response).
    """


def is_recoverable_http_status(status_code: int) -> bool:
    """Returns True if the HTTP status code is in the Recoverable_HTTP_Status set."""
    return status_code in RECOVERABLE_HTTP_STATUSES
