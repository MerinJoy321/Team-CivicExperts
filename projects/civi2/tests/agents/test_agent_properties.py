"""Property-based tests for Agent Layer (Tasks 14.3, 15.4).

# Feature: civicpilot, Property 17: Search plans are always sized 3-5 and pairwise distinct
# Feature: civicpilot, Property 14: Document generation only occurs for HIGH-confidence ELIGIBLE results with complete info

Validates: Requirements 8.1, 8.3, 15.1, 15.2
"""

from __future__ import annotations

import asyncio
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from civicpilot.agents.document_advisor_agent import DocumentAdvisorAgent
from civicpilot.agents.errors import PlanningFailure
from civicpilot.agents.models import EligibilityResult, SchemeCandidate
from civicpilot.agents.planner_agent import SearchOperation, SearchPlan
from civicpilot.scheduler.models import ToolCategory
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.tools.document_generator import DocumentGenerator


@st.composite
def search_ops_strategy(draw):
    n = draw(st.integers(min_value=1, max_value=8))
    queries = draw(st.lists(st.text(alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd')), min_size=3, max_size=20), min_size=n, max_size=n))
    return [SearchOperation(op_id=f"op_{i}", query=q) for i, q in enumerate(queries)]


@given(ops=search_ops_strategy())
@settings(max_examples=100)
def test_property_17_search_plan_shape_and_distinctness(ops: list[SearchOperation]):
    # Feature: civicpilot, Property 17: Search plans are always sized 3-5 and pairwise distinct
    queries_norm = [o.query.strip().lower() for o in ops]
    is_distinct = len(set(queries_norm)) == len(queries_norm)
    is_valid_size = 3 <= len(ops) <= 5

    if is_valid_size and is_distinct:
        plan = SearchPlan(operations=ops)
        assert len(plan.operations) == len(ops)
    else:
        with pytest.raises(PlanningFailure):
            SearchPlan(operations=ops)


@given(
    overall=st.sampled_from(["ELIGIBLE", "NOT_ELIGIBLE", "POSSIBLE_NEEDS_INFO"]),
    confidence=st.sampled_from(["HIGH", "MEDIUM", "LOW"]),
    identity_complete=st.booleans(),
    application_complete=st.booleans(),
)
@settings(max_examples=100)
def test_property_14_document_generation_gate(
    overall: str, confidence: str, identity_complete: bool, application_complete: bool
):
    # Feature: civicpilot, Property 14: Document generation only occurs for HIGH-confidence ELIGIBLE results with complete info
    scheduler = Scheduler(
        concurrency_limits={ToolCategory.DOCUMENT: 2},
        executor=lambda task: "ok",
    )
    generator = DocumentGenerator()
    advisor = DocumentAdvisorAgent(generator, scheduler)

    result = EligibilityResult(
        scheme_id="scheme_1",
        overall=overall,  # type: ignore[arg-type]
        confidence_level=confidence,  # type: ignore[arg-type]
    )
    candidate = SchemeCandidate(
        scheme_id="scheme_1",
        name="Test Scheme",
        identity_info_complete=identity_complete,
        application_info_complete=application_complete,
    )

    async def _test():
        outcome = await advisor.maybe_generate(result, candidate)

        expected_pass = (
            overall == "ELIGIBLE"
            and confidence == "HIGH"
            and identity_complete is True
            and application_complete is True
        )

        assert outcome.generated is expected_pass

    asyncio.run(_test())
