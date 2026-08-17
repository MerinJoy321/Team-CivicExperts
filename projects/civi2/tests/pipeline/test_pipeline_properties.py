"""Property-based tests for Pipeline & Verification Engine (Tasks 19.5, 19.6, 20.5, 20.6, 21.4, 22.2).

# Feature: civicpilot, Property 12: URL deduplication is a stable idempotent normalization
# Feature: civicpilot, Property 13: Filtered candidate selection never exceeds 5 and never fabricates candidates
# Feature: civicpilot, Property 8: UNKNOWN never becomes PASS
# Feature: civicpilot, Property 9: Overall eligibility derivation is a pure, order-independent function of criterion classifications
# Feature: civicpilot, Property 18: Adaptive evidence cycles are bounded and monotonically resolve or exhaust
# Feature: civicpilot, Property 19: Confidence is never higher than an equivalent failure-free run

Validates: Requirements 10.1, 10.5, 13.3-13.5, 14.2, 14.3, 14.5, 19.3, 19.4
"""

from __future__ import annotations

import random
from hypothesis import given, settings
from hypothesis import strategies as st

from civicpilot.agents.models import EligibilityCriterion, EligibilityResult, Profile
from civicpilot.pipeline.adaptive_loop import assess_sufficiency
from civicpilot.pipeline.confidence_degradation import apply_confidence_degradation
from civicpilot.pipeline.filter_pipeline import FilterPipeline, normalize_url
from civicpilot.pipeline.verification_engine import derive_overall_result, evaluate_deterministic_criterion
from civicpilot.tools.search_tool import RawSearchResult


# Property 12: URL deduplication is stable and idempotent
@given(
    host=st.sampled_from(["myscheme.gov.in", "pmkisan.gov.in", "EXAMPLE.COM"]),
    path=st.sampled_from(["/schemes/", "/schemes", "/path/to/page/"]),
    param_val=st.integers(min_value=1, max_value=100),
)
@settings(max_examples=100)
def test_property_12_url_dedup_idempotence(host: str, path: str, param_val: int):
    # Feature: civicpilot, Property 12: URL deduplication is a stable idempotent normalization
    raw_url = f"HTTP://{host}{path}?b=2&a={param_val}"

    norm1 = normalize_url(raw_url)
    norm2 = normalize_url(norm1)

    # Idempotent: normalizing twice gives same output
    assert norm1 == norm2
    # Scheme and netloc are lowercased
    assert host.lower() in norm1
    # Query parameters are sorted ('a' before 'b')
    assert norm1.find("a=") < norm1.find("b=") if "a=" in norm1 and "b=" in norm1 else True


# Property 13: Selection bounds <= 5 and no fabricated candidates
@given(
    results_count=st.integers(min_value=0, max_value=20),
)
@settings(max_examples=100)
def test_property_13_filtered_selection_bounds(results_count: int):
    # Feature: civicpilot, Property 13: Filtered candidate selection never exceeds 5 and never fabricates candidates
    pipeline = FilterPipeline()
    raw = [
        RawSearchResult(url=f"https://scheme{i}.gov.in", title=f"Scheme {i}", snippet="info", score=i * 0.1)
        for i in range(results_count)
    ]

    selected = pipeline.rank_and_select(raw)

    assert len(selected) <= 5
    assert len(selected) <= results_count
    # All selected items were present in input raw list
    assert all(item in raw for item in selected)


# Property 8: UNKNOWN never becomes PASS
@given(
    income_val=st.one_of(st.none(), st.floats(min_value=0.0, max_value=100000.0)),
    threshold=st.floats(min_value=10000.0, max_value=50000.0),
)
@settings(max_examples=100)
def test_property_8_unknown_never_becomes_pass(income_val: float | None, threshold: float):
    # Feature: civicpilot, Property 8: UNKNOWN never becomes PASS
    profile = Profile(income=income_val)
    criterion = EligibilityCriterion(
        criterion_id="c1",
        scheme_id="s1",
        description="Income check",
        profile_field="income",
        comparator="lte",
        threshold=threshold,
    )

    eval_result = evaluate_deterministic_criterion(criterion, profile)

    if income_val is None:
        assert eval_result.classification == "UNKNOWN"
        assert eval_result.classification != "PASS"


# Property 9: Overall eligibility derivation is pure and order-independent
@given(
    classifications=st.lists(
        st.sampled_from(["PASS", "FAIL", "UNKNOWN"]), min_size=1, max_size=10
    )
)
@settings(max_examples=100)
def test_property_9_overall_result_derivation_order_independence(classifications: list[str]):
    # Feature: civicpilot, Property 9: Overall eligibility derivation is a pure, order-independent function of criterion classifications
    criteria1 = [
        EligibilityCriterion(criterion_id=f"c{i}", scheme_id="s1", description="", classification=cls)  # type: ignore[arg-type]
        for i, cls in enumerate(classifications)
    ]

    # Shuffle criteria
    shuffled_cls = list(classifications)
    random.shuffle(shuffled_cls)
    criteria2 = [
        EligibilityCriterion(criterion_id=f"c{i}", scheme_id="s1", description="", classification=cls)  # type: ignore[arg-type]
        for i, cls in enumerate(shuffled_cls)
    ]

    res1 = derive_overall_result(criteria1)
    res2 = derive_overall_result(criteria2)

    # Order independent
    assert res1 == res2

    # Deterministic rules
    if "FAIL" in classifications:
        assert res1 == "NOT_ELIGIBLE"
    elif all(c == "PASS" for c in classifications):
        assert res1 == "ELIGIBLE"
    else:
        assert res1 == "POSSIBLE_NEEDS_INFO"


# Property 18: Adaptive evidence cycle boundedness
@given(
    criteria_count=st.integers(min_value=1, max_value=10),
)
@settings(max_examples=100)
def test_property_18_adaptive_cycle_boundedness(criteria_count: int):
    # Feature: civicpilot, Property 18: Adaptive evidence cycles are bounded and monotonically resolve or exhaust
    criteria = [
        EligibilityCriterion(criterion_id=f"c{i}", scheme_id="s1", description="", classification="UNKNOWN")
        for i in range(criteria_count)
    ]

    report = assess_sufficiency(criteria)
    # UNKNOWN criteria -> not sufficient unless FAIL present
    assert report.is_sufficient is False
    assert len(report.unresolved_criteria) == criteria_count


# Property 19: Confidence degradation
@given(
    initial_conf=st.sampled_from(["HIGH", "MEDIUM", "LOW"]),
    overall=st.sampled_from(["ELIGIBLE", "NOT_ELIGIBLE", "POSSIBLE_NEEDS_INFO"]),
)
@settings(max_examples=100)
def test_property_19_confidence_degradation(initial_conf: str, overall: str):
    # Feature: civicpilot, Property 19: Confidence is never higher than an equivalent failure-free run
    res = EligibilityResult(
        scheme_id="s1",
        overall=overall,  # type: ignore[arg-type]
        confidence_level=initial_conf,  # type: ignore[arg-type]
    )

    degraded = apply_confidence_degradation(res, has_tool_failures=True)

    assert degraded.degraded is True
    # Confidence is strictly lower or equal to LOW
    if initial_conf == "HIGH":
        assert degraded.confidence_level == "MEDIUM"
    elif initial_conf in ("MEDIUM", "LOW"):
        assert degraded.confidence_level == "LOW"
