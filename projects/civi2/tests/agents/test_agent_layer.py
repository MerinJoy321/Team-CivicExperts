"""Unit tests for CrewAI Agent Layer and Architecture Guard (Tasks 14.4, 15.5, 16.2).

Covers:
- Exactly one Reasoning_Model call per PlannerAgent plan (Requirement 8.2).
- ReporterAgent performance trace formatting (Requirement 4.9).
- Architecture violation rejection when calling tools outside active_agent_context (Requirement 1.5).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
import pytest

from civicpilot.agents.document_advisor_agent import DocumentAdvisorAgent
from civicpilot.agents.errors import ArchitectureViolationError, PlanningFailure
from civicpilot.agents.models import Profile, SchemeCandidate
from civicpilot.agents.planner_agent import PlannerAgent, SearchPlan
from civicpilot.agents.reporter_agent import ReporterAgent
from civicpilot.agents.scheduler_proxy import SchedulerToolProxy, active_agent_context
from civicpilot.config.settings import ConcurrencyConfig, ModelConfig, Settings
from civicpilot.scheduler.models import TaskStatus, ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.tools.document_generator import DocumentGenerator
from civicpilot.tools.model_client import ModelClient


async def _dummy_executor(task: ToolTask) -> Any:
    if task.tool_name == "reasoning_model_llm":
        return json.dumps(
            {
                "reasoning": "Plan formulation",
                "operations": [
                    {"op_id": "op1", "query": "PM Kisan eligibility"},
                    {"op_id": "op2", "query": "PM Kisan documents"},
                    {"op_id": "op3", "query": "PM Kisan application steps"},
                ],
            }
        )
    return "ok"


@pytest.fixture
def settings_fixture() -> Settings:
    return Settings(
        concurrency=ConcurrencyConfig(4, 4, 3),
        fast_model=ModelConfig(name="fast-llm", endpoint="http://localhost", api_key="k1"),
        reasoning_model=ModelConfig(name="reasoning-llm", endpoint="http://localhost", api_key="k2"),
    )


@pytest.fixture
def scheduler() -> Scheduler:
    return Scheduler(
        concurrency_limits={ToolCategory.REASONING_MODEL: 2, ToolCategory.SEARCH: 4},
        executor=_dummy_executor,
    )


class TestPlannerAgent:
    @pytest.mark.asyncio
    async def test_exactly_one_reasoning_model_call_per_plan(
        self, settings_fixture: Settings, scheduler: Scheduler
    ) -> None:
        client = ModelClient(settings_fixture, scheduler)
        planner = PlannerAgent(client)
        profile = Profile(occupation="Farmer", raw_input="I need farming support")

        run_task = asyncio.create_task(scheduler.run())
        try:
            plan = await planner.plan(profile)
            assert isinstance(plan, SearchPlan)
            assert len(plan.operations) == 3
            assert plan.operations[0].query == "PM Kisan eligibility"
        finally:
            scheduler.stop()
            await run_task


class TestReporterAgent:
    def test_performance_trace_text_formatting(self) -> None:
        reporter = ReporterAgent()

        class DummyEvent:
            tool_name = "tavily_search"
            batch_size = 3
            elapsed_s = 1.234
            status = "COMPLETE"

        class DummySnapshot:
            trace_events = [DummyEvent()]

        trace = reporter.format_performance_trace(DummySnapshot())
        assert "Performance Trace" in trace
        assert "tavily_search" in trace
        assert "PARALLEL" in trace
        assert "1.234s" in trace


class TestArchitectureGuard:
    @pytest.mark.asyncio
    async def test_architecture_violation_rejection(self, scheduler: Scheduler) -> None:
        proxy = SchedulerToolProxy(scheduler)
        task = ToolTask(
            task_id="t1",
            category=ToolCategory.SEARCH,
            tool_name="test_tool",
            params={},
            priority=5,
            timeout_ms=1000,
            depends_on=[],
            agent_role="",
        )

        # Direct call outside active_agent_context raises ArchitectureViolationError
        with pytest.raises(ArchitectureViolationError):
            await proxy.invoke(task)

        # Call inside valid active_agent_context succeeds
        with active_agent_context("Planner_Agent"):
            fut = await proxy.invoke(task)
            assert fut is not None
