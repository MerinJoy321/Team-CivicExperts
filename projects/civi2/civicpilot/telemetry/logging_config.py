"""Structured diagnostic logging for CivicPilot (Task 1.3).

This module configures Python's standard ``logging`` module to emit
structured, single-line JSON records to stderr, and exposes a
``get_logger()`` helper that the Scheduler, tools, agents, and pipeline
modules import to obtain a consistently configured ``logging.Logger``.

Placement note: this lives under ``civicpilot.telemetry`` (rather than
``civicpilot.config``) because it is operational/diagnostic instrumentation
in the same spirit as the timing/instrumentation utilities landing in Task
1.4 in this same package -- both exist to observe what the System is doing
internally. ``civicpilot.config`` is reserved for environment-driven
*configuration values* (concurrency limits, model provider settings), which
this module reads from (``LOG_LEVEL``) but does not otherwise belong with.

Important distinction (Requirement 21 / design "Security and Trace
Confidentiality"): this logger is an **internal diagnostic** channel, not
the citizen-facing Streaming_UI trace. The Streaming_UI trace has strict,
mandatory secret-redaction and field-limiting rules (Requirements 21.1-21.4)
because its output is shown directly to citizens. This module's logs are
operator-facing only (stderr / log aggregation), so they may be more
verbose than the Streaming_UI trace. As a good-practice precaution (not a
substitute for the Streaming_UI's full redaction machinery, which is a
separate, later task), this module applies a lightweight best-effort mask
to obviously credential-shaped strings (e.g. ``api_key=...``,
``Bearer ...``, ``password: ...``) before emitting a record at INFO level
or above. This is intentionally simple -- it is not exhaustive secret
detection.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Mapping

__all__ = ["get_logger", "configure_logging"]

_CONFIGURED = False
_LOG_LEVEL_ENV_VAR = "LOG_LEVEL"
_DEFAULT_LOG_LEVEL = "INFO"

# Reserved attributes on a standard LogRecord. Anything else the caller
# passes via ``extra={...}`` is treated as a structured extra field and
# merged into the emitted JSON record.
_RESERVED_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__.keys()) | {
    "message",
    "asctime",
}

# Best-effort, non-exhaustive patterns for obviously credential-shaped
# strings. This is a basic caution for the internal diagnostic logger only
# -- NOT the full redaction machinery required for the citizen-facing
# Streaming_UI trace (Requirements 21.1, 21.2), which is implemented
# separately.
_CREDENTIAL_SHAPED_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)([^\s'\"&]+)"),
    re.compile(r"(?i)(authorization\s*:\s*Bearer\s+)([^\s'\"]+)"),
    re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9._~+/=-]{8,})"),
    re.compile(r"(?i)(password\s*[=:]\s*)([^\s'\"&]+)"),
    re.compile(r"(?i)(secret\s*[=:]\s*)([^\s'\"&]+)"),
    re.compile(r"(?i)(token\s*[=:]\s*)([^\s'\"&]+)"),
]

_REDACTED_MARKER = "***REDACTED***"


def _mask_credential_shaped(text: str) -> str:
    """Best-effort masking of obviously credential-shaped substrings.

    This is a basic caution applied to internal diagnostic log messages at
    INFO level and above. It is intentionally simple and NOT a substitute
    for the dedicated secret-redaction pass required for the citizen-facing
    Streaming_UI trace (a separate, future task).
    """
    masked = text
    for pattern in _CREDENTIAL_SHAPED_PATTERNS:
        masked = pattern.sub(lambda m: f"{m.group(1)}{_REDACTED_MARKER}", masked)
    return masked


class _JsonLinesFormatter(logging.Formatter):
    """Formats each LogRecord as a single line of JSON.

    Fields always present: ``timestamp`` (ISO-8601 UTC), ``logger``,
    ``level``, ``message``. Any additional fields passed via
    ``logger.info(msg, extra={...})`` are included as-is under their given
    keys (as long as they don't collide with a reserved LogRecord
    attribute name).
    """

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.levelno >= logging.INFO:
            message = _mask_credential_shaped(message)

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "logger": record.name,
            "level": record.levelname,
            "message": message,
        }

        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS:
                continue
            payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def _resolve_log_level() -> int:
    """Read the desired log level from the ``LOG_LEVEL`` environment
    variable, defaulting to INFO. Falls back to INFO (rather than raising)
    if the configured value is not a recognized level name.
    """
    raw = os.environ.get(_LOG_LEVEL_ENV_VAR, _DEFAULT_LOG_LEVEL).strip().upper()
    level = logging.getLevelName(raw)
    if isinstance(level, int):
        return level
    return logging.INFO


def configure_logging(*, force: bool = False) -> None:
    """Configure the root ``civicpilot`` logger hierarchy for structured
    JSON-lines output to stderr.

    Idempotent: calling this more than once is a no-op unless ``force=True``,
    so importing ``get_logger`` from multiple modules does not attach
    duplicate handlers. The level is re-read from ``LOG_LEVEL`` every time
    this function actually (re)configures, so tests can flip the
    environment variable and call ``configure_logging(force=True)`` to pick
    up the change.
    """
    global _CONFIGURED

    if _CONFIGURED and not force:
        return

    root = logging.getLogger("civicpilot")
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(_JsonLinesFormatter())
    root.addHandler(handler)
    root.setLevel(_resolve_log_level())
    root.propagate = False

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a consistently configured ``logging.Logger`` for ``name``.

    This is the entry point Scheduler, tool, agent, and pipeline modules
    should use for diagnostic output, e.g.::

        from civicpilot.telemetry.logging_config import get_logger

        logger = get_logger(__name__)
        logger.info("dispatching task", extra={"task_id": task.task_id})

    All loggers returned by this helper are children of a single
    ``civicpilot`` root logger that emits structured JSON lines to stderr
    and whose level is controlled by the ``LOG_LEVEL`` environment variable
    (default ``INFO``). If ``name`` does not already start with
    ``civicpilot``, it is nested under the ``civicpilot`` namespace so that
    the level/handler configuration on the root applies to it.
    """
    configure_logging()

    if name == "civicpilot" or name.startswith("civicpilot."):
        qualified_name = name
    else:
        qualified_name = f"civicpilot.{name}"

    return logging.getLogger(qualified_name)
