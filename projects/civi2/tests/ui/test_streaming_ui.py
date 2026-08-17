"""Unit and property-based tests for StreamingUI and Secret Redaction (Tasks 25.5, 25.6).

# Feature: civicpilot, Property 20: Trace events never contain secret-shaped values and always respect field limits

Validates: Requirements 20.6, 21.1, 21.2, 21.4
"""

from __future__ import annotations

import asyncio
import inspect
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from civicpilot.agents.models import TraceEvent
from civicpilot.ui.async_transport import InMemoryAsyncTransport
from civicpilot.ui.streaming_ui import StreamingUI


@given(
    secret_key=st.sampled_from(["api_key", "password", "token", "secret", "bearer"]),
    secret_val=st.text(alphabet=st.characters(whitelist_categories=('Nd',)), min_size=10, max_size=30),
    desc=st.text(alphabet=st.characters(whitelist_categories=('Lu', 'Ll')), min_size=1, max_size=150),
    summary=st.text(alphabet=st.characters(whitelist_categories=('Lu', 'Ll')), min_size=1, max_size=350),
)
@settings(max_examples=100)
def test_property_20_trace_event_safety_and_field_limits(
    secret_key: str, secret_val: str, desc: str, summary: str
):
    # Feature: civicpilot, Property 20: Trace events never contain secret-shaped values and always respect field limits
    transport = InMemoryAsyncTransport()
    ui = StreamingUI(transport)

    secret_str = f"{secret_key}={secret_val}"
    event = TraceEvent(
        tool_name="tavily_search",
        operation_description=f"{desc} {secret_str}",
        status="COMPLETE",
        elapsed_s=1.23,
        result_summary=f"{summary} {secret_str}",
    )

    async def _test():
        await ui.publish(event)
        published = transport.published_events[0]

        # 1. Field bounds enforced
        assert len(published.operation_description) <= 200
        assert len(published.result_summary) <= 500

        # 2. Raw secret value absent
        assert secret_val not in published.operation_description
        assert secret_val not in published.result_summary

    asyncio.run(_test())


class TestStreamingUINoTimer:
    def test_publish_call_path_has_no_timer_driven_loops(self) -> None:
        """Requirement 20.6: Asserts publish call path contains no asyncio.sleep or timer loops."""
        source = inspect.getsource(StreamingUI.publish)
        # Strip docstrings and comments
        code_lines = [line for line in source.splitlines() if not line.strip().startswith('"""') and not line.strip().startswith("#")]
        code_body = "\n".join(code_lines)

        assert "asyncio.sleep" not in code_body
        assert "call_later" not in code_body
        assert "call_at" not in code_body
