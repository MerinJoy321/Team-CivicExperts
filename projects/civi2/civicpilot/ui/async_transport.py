"""AsyncTransport interface protocol for WebSocket/SSE transport (Requirement 20.6)."""

from __future__ import annotations

from typing import Protocol

from civicpilot.agents.models import TraceEvent


class AsyncTransport(Protocol):
    """Abstract async transport for StreamingUI event publishing."""

    async def send_event(self, event: TraceEvent) -> None: ...


class InMemoryAsyncTransport:
    """In-memory transport for testing and local streaming UI events."""

    def __init__(self) -> None:
        self.published_events: list[TraceEvent] = []

    async def send_event(self, event: TraceEvent) -> None:
        self.published_events.append(event)
