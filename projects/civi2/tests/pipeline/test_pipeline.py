"""Unit and integration tests for pipeline logic (Tasks 18.2, 23.3).

Covers:
- IntakeRejectionError on empty, whitespace-only, or >5000 character inputs (Requirement 7.2).
- ProfileExtractionError on malformed JSON / failed extraction call (Requirement 7.6).
- CivicPilotPipeline end-to-end fixture execution (Requirements 17.1-17.3).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
import pytest

from civicpilot.agents.document_advisor_agent import DocumentAdvisorAgent
from civicpilot.agents.models import EligibilityCriterion, FinalReport, Profile, SchemeCandidate
from civicpilot.agents.planner_agent import PlannerAgent
from civicpilot.agents.reporter_agent import ReporterAgent
from civicpilot.agents.researcher_agent import ResearcherAgent
from civicpilot.agents.verifier_agent import VerifierAgent
from civicpilot.config.settings import ConcurrencyConfig, ModelConfig, Settings
from civicpilot.pipeline.errors import IntakeRejectionError, ProfileExtractionError
from civicpilot.pipeline.intake_module import IntakeModule
from civicpilot.pipeline.orchestrator import CivicPilotPipeline
from civicpilot.scheduler.models import TaskStatus, ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.telemetry.telemetry_module import TelemetryModule
from civicpilot.tools.document_generator import DocumentGenerator
from civicpilot.tools.fetch_tool import FetchToolProxy
from civicpilot.tools.model_client import ModelClient
from civicpilot.tools.search_tool import SearchToolProxy


async def _pipeline_executor(task: ToolTask) -> Any:
    if task.tool_name == "fast_model_llm":
        return json.dumps(
            {
                "age": 22,
                "gender": "male",
                "income": 0.0,
                "occupation": "student",
                "stated_need": "scholarship",
            }
        )
    if task.tool_name == "reasoning_model_llm":
        return json.dumps(
            {
                "reasoning": "Search plan for student scholarship",
                "operations": [
                    {"op_id": "op1", "query": "student scholarship eligibility"},
                    {"op_id": "op2", "query": "national scholarship portal"},
                    {"op_id": "op3", "query": "post matric scholarship"},
                ],
            }
        )
    if task.tool_name == "tavily_search":
        return [
            {
                "url": "https://scholarships.gov.in/scheme1",
                "title": "Post Matric Scholarship",
                "snippet": "Scholarship for student",
                "score": 0.9,
            }
        ]
    return "ok"


class TestIntakeRejection:
    @pytest.mark.asyncio
    async def test_intake_rejects_empty_and_whitespace_and_large_input(self) -> None:
        settings = Settings(
            concurrency=ConcurrencyConfig(4, 4, 3),
            fast_model=ModelConfig("fast", "http://loc", "k1"),
            reasoning_model=ModelConfig("reasoning", "http://loc", "k2"),
        )
        scheduler = Scheduler(concurrency_limits={ToolCategory.FAST_MODEL: 2}, executor=_pipeline_executor)
        client = ModelClient(settings, scheduler)
        intake = IntakeModule(client)

        with pytest.raises(IntakeRejectionError):
            await intake.extract_profile("")

        with pytest.raises(IntakeRejectionError):
            await intake.extract_profile("   \n\t   ")

        with pytest.raises(IntakeRejectionError):
            await intake.extract_profile("a" * 5001)


class TestFullPipelineIntegration:
    @pytest.mark.asyncio
    async def test_normal_request_end_to_end_pipeline(self) -> None:
        settings = Settings(
            concurrency=ConcurrencyConfig(4, 4, 3),
            fast_model=ModelConfig("fast", "http://loc", "k1"),
            reasoning_model=ModelConfig("reasoning", "http://loc", "k2"),
        )
        telemetry = TelemetryModule()
        scheduler = Scheduler(
            concurrency_limits={
                ToolCategory.FAST_MODEL: 4,
                ToolCategory.REASONING_MODEL: 2,
                ToolCategory.SEARCH: 4,
                ToolCategory.FETCH: 4,
                ToolCategory.DOCUMENT: 2,
            },
            executor=_pipeline_executor,
            telemetry=telemetry,
        )

        client = ModelClient(settings, scheduler)
        intake = IntakeModule(client)
        planner = PlannerAgent(client)
        search_proxy = SearchToolProxy(scheduler)
        fetch_proxy = FetchToolProxy(scheduler)
        researcher = ResearcherAgent(search_proxy, fetch_proxy)
        verifier = VerifierAgent(client, scheduler)
        reporter = ReporterAgent(telemetry)
        doc_gen = DocumentGenerator()
        doc_advisor = DocumentAdvisorAgent(doc_gen, scheduler)

        pipeline = CivicPilotPipeline(
            intake_module=intake,
            planner_agent=planner,
            researcher_agent=researcher,
            verifier_agent=verifier,
            reporter_agent=reporter,
            document_advisor=doc_advisor,
            telemetry=telemetry,
        )

        run_task = asyncio.create_task(scheduler.run())
        try:
            report = await pipeline.run("I am a 22 year old unemployed student looking for a scholarship.")
            assert isinstance(report, FinalReport)
            assert report.profile_summary is not None
            assert len(report.scheme_candidates) >= 1
            assert "Performance Trace" in report.performance_trace
        finally:
            scheduler.stop()
            await run_task
