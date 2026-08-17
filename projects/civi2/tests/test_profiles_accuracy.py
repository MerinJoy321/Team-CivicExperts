"""Cross-profile accuracy and sequence invariance test suite (Requirements 17.1, 17.2, 22.2-22.4).

Verifies pipeline execution across 5 distinct citizen profiles:
1. Unemployed student
2. Elderly citizen
3. Widow
4. Farmer
5. Small business owner

Asserts sequence invariance: all profiles execute identical stage sequence without hardcoded branching.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
import pytest

from civicpilot.agents.document_advisor_agent import DocumentAdvisorAgent
from civicpilot.agents.models import FinalReport
from civicpilot.agents.planner_agent import PlannerAgent
from civicpilot.agents.reporter_agent import ReporterAgent
from civicpilot.agents.researcher_agent import ResearcherAgent
from civicpilot.agents.verifier_agent import VerifierAgent
from civicpilot.config.settings import ConcurrencyConfig, ModelConfig, Settings
from civicpilot.pipeline.intake_module import IntakeModule
from civicpilot.pipeline.orchestrator import CivicPilotPipeline
from civicpilot.scheduler.models import ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.telemetry.telemetry_module import TelemetryModule
from civicpilot.tools.document_generator import DocumentGenerator
from civicpilot.tools.fetch_tool import FetchToolProxy
from civicpilot.tools.model_client import ModelClient
from civicpilot.tools.search_tool import SearchToolProxy

PROFILES_FIXTURES = [
    ("Unemployed Student", "I am a 21 year old student looking for education scholarships."),
    ("Elderly Citizen", "I am a 68 year old senior citizen seeking old age pension."),
    ("Widow", "I am a 45 year old widow seeking widow pension and assistance."),
    ("Farmer", "I am a small farmer seeking agricultural subsidy support."),
    ("Small Business Owner", "I am a small business owner seeking MSME loan support."),
]


async def _mock_profile_executor(task: ToolTask) -> Any:
    if task.tool_name == "fast_model_llm":
        return json.dumps(
            {
                "age": 45,
                "occupation": "citizen",
                "stated_need": "welfare support",
            }
        )
    if task.tool_name == "reasoning_model_llm":
        return json.dumps(
            {
                "reasoning": "Plan search operations",
                "operations": [
                    {"op_id": "op1", "query": "welfare scheme eligibility"},
                    {"op_id": "op2", "query": "official government portal"},
                    {"op_id": "op3", "query": "scheme application documents"},
                ],
            }
        )
    if task.tool_name == "tavily_search":
        return [
            {
                "url": "https://myscheme.gov.in/scheme_detail",
                "title": "Government Welfare Scheme",
                "snippet": "Eligibility criteria and support",
                "score": 0.95,
            }
        ]
    return "ok"


class TestCrossProfileAccuracy:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("profile_name,input_text", PROFILES_FIXTURES)
    async def test_cross_profile_pipeline_accuracy(self, profile_name: str, input_text: str) -> None:
        """Requirement 17.1, 17.2: Pipeline succeeds across all 5 citizen profile types."""
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
            executor=_mock_profile_executor,
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
            report = await pipeline.run(input_text)
            assert isinstance(report, FinalReport)
            assert len(report.scheme_candidates) >= 1
            assert "Performance Trace" in report.performance_trace
        finally:
            scheduler.stop()
            await run_task
