"""Unit tests for ModelClient (Task 11.3).

Covers:
- Fast_Model and Reasoning_Model timeouts bounded by configured ranges (Requirements 18.4, 18.5).
- ModelClient reading Settings configuration for Fast_Model and Reasoning_Model roles (Requirements 6.1, 6.2).
"""

from __future__ import annotations

import asyncio
import pytest

from civicpilot.config.settings import ConcurrencyConfig, ModelConfig, Settings
from civicpilot.scheduler.models import ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.tools.model_client import ModelClient


async def _dummy_executor(task: ToolTask) -> str:
    return "llm_output"


@pytest.fixture
def settings_fixture() -> Settings:
    return Settings(
        concurrency=ConcurrencyConfig(4, 4, 3),
        fast_model=ModelConfig(name="fast-llm-v1", endpoint="http://localhost:8000", api_key="key1"),
        reasoning_model=ModelConfig(name="reasoning-llm-v1", endpoint="http://localhost:8000", api_key="key2"),
    )


@pytest.fixture
def scheduler() -> Scheduler:
    return Scheduler(
        concurrency_limits={ToolCategory.FAST_MODEL: 4, ToolCategory.REASONING_MODEL: 2},
        executor=_dummy_executor,
    )


class TestModelClient:
    @pytest.mark.asyncio
    async def test_fast_model_call_routing_and_timeout(self, settings_fixture: Settings, scheduler: Scheduler) -> None:
        client = ModelClient(settings_fixture, scheduler)
        run_task = asyncio.create_task(scheduler.run())
        try:
            future = await client.call_fast_model("Extract profile")
            finished_task = await asyncio.wait_for(future, timeout=2.0)
            assert isinstance(finished_task, ToolTask)
            assert finished_task.category == ToolCategory.FAST_MODEL
            assert finished_task.tool_name == "fast_model_llm"
            assert finished_task.params["model_name"] == "fast-llm-v1"
            assert 2000 <= finished_task.timeout_ms <= 4000
        finally:
            scheduler.stop()
            await run_task

    @pytest.mark.asyncio
    async def test_reasoning_model_call_routing_and_timeout(
        self, settings_fixture: Settings, scheduler: Scheduler
    ) -> None:
        client = ModelClient(settings_fixture, scheduler)
        run_task = asyncio.create_task(scheduler.run())
        try:
            future = await client.call_reasoning_model("Formulate search plan")
            finished_task = await asyncio.wait_for(future, timeout=2.0)
            assert isinstance(finished_task, ToolTask)
            assert finished_task.category == ToolCategory.REASONING_MODEL
            assert finished_task.tool_name == "reasoning_model_llm"
            assert finished_task.params["model_name"] == "reasoning-llm-v1"
            assert 15000 <= finished_task.timeout_ms <= 30000
        finally:
            scheduler.stop()
            await run_task
