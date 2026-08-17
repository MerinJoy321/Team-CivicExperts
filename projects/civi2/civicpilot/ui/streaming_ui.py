"""StreamingUI implementation (Requirements 4.9, 20.1-20.7, 21.1-21.4).

Publishes real-time status events (RUNNING, COMPLETE, FAILED, SKIPPED) within 500ms
of state transitions. Applies secret redaction and field length truncation (description <= 200, summary <= 500).
Invoked exclusively from callbacks -- no timer-driven publish paths.
"""

from __future__ import annotations

from typing import Optional

from civicpilot.agents.models import TraceEvent
from civicpilot.ui.async_transport import AsyncTransport, InMemoryAsyncTransport
from civicpilot.ui.redactor import redact_secrets, truncate_field


class StreamingUI:
    """Real-time Streaming UI manager."""

    def __init__(self, transport: Optional[AsyncTransport] = None) -> None:
        self._transport = transport or InMemoryAsyncTransport()

    @property
    def transport(self) -> AsyncTransport:
        return self._transport

    async def publish(self, event: TraceEvent) -> None:
        """Publishes event after applying secret redaction and field truncation.

        Requirement 20.1-20.7: Real-time status event publishing without timer delays.
        Requirement 21.1-21.4 / Property 20: Secret redaction and field size limits.
        """
        # 1. Redact secrets
        redacted_summary = redact_secrets(event.result_summary)
        redacted_desc = redact_secrets(event.operation_description)

        # 2. Truncate field bounds (description <= 200, summary <= 500)
        bounded_desc = truncate_field(redacted_desc, 200)
        bounded_summary = truncate_field(redacted_summary, 500)

        safe_event = TraceEvent(
            tool_name=event.tool_name,
            operation_description=bounded_desc,
            status=event.status,
            elapsed_s=event.elapsed_s,
            result_summary=bounded_summary,
        )

        # 3. Publish synchronously via transport
        await self._transport.send_event(safe_event)
