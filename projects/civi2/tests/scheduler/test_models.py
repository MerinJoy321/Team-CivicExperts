"""Unit tests for civicpilot.scheduler.models (Task 3.1).

Covers valid construction with all fields, default field values,
priority/timeout_ms boundary validation, and that the ToolCategory/
TaskStatus enum values match the design's exact names.
"""

from __future__ import annotations

import pytest

from civicpilot.scheduler import TaskStatus, ToolCategory, ToolTask
from civicpilot.scheduler.models import SKIP_PROPAGATING_STATUSES


def _make_task(**overrides):
    defaults = dict(
        task_id="t1",
        category=ToolCategory.SEARCH,
        tool_name="tavily_search",
        params={"query": "pm kisan eligibility"},
        priority=1,
        timeout_ms=6000,
        depends_on=[],
        agent_role="Researcher_Agent",
    )
    defaults.update(overrides)
    return ToolTask(**defaults)


# ---------------------------------------------------------------------------
# Enum value checks (Requirement 2.1; design's exact ToolCategory/TaskStatus).
# ---------------------------------------------------------------------------


def test_tool_category_values_match_design():
    assert {c.value for c in ToolCategory} == {
        "search",
        "fetch",
        "verify",
        "fast_model",
        "reasoning_model",
        "portal_validate",
        "document",
        "cache",
    }
    assert ToolCategory.SEARCH == "search"
    assert ToolCategory.FETCH == "fetch"
    assert ToolCategory.VERIFY == "verify"
    assert ToolCategory.FAST_MODEL == "fast_model"
    assert ToolCategory.REASONING_MODEL == "reasoning_model"
    assert ToolCategory.PORTAL_VALIDATE == "portal_validate"
    assert ToolCategory.DOCUMENT == "document"
    assert ToolCategory.CACHE == "cache"


def test_task_status_values_match_design():
    assert {s.value for s in TaskStatus} == {
        "pending",
        "eligible",
        "running",
        "completed",
        "failed",
        "timed_out",
        "cancelled",
        "skipped",
    }
    assert TaskStatus.PENDING == "pending"
    assert TaskStatus.ELIGIBLE == "eligible"
    assert TaskStatus.RUNNING == "running"
    assert TaskStatus.COMPLETED == "completed"
    assert TaskStatus.FAILED == "failed"
    assert TaskStatus.TIMED_OUT == "timed_out"
    assert TaskStatus.CANCELLED == "cancelled"
    assert TaskStatus.SKIPPED == "skipped"


def test_skip_propagating_statuses_match_requirement_2_11():
    assert SKIP_PROPAGATING_STATUSES == {
        TaskStatus.FAILED,
        TaskStatus.TIMED_OUT,
        TaskStatus.CANCELLED,
    }


# ---------------------------------------------------------------------------
# Valid construction with all fields.
# ---------------------------------------------------------------------------


def test_valid_construction_with_all_fields():
    task = ToolTask(
        task_id="task-1",
        category=ToolCategory.FETCH,
        tool_name="jina_fetch",
        params={"url": "https://example.gov.in/scheme"},
        priority=3,
        timeout_ms=9000,
        depends_on=["task-0"],
        agent_role="Researcher_Agent",
        status=TaskStatus.ELIGIBLE,
        retries_used=1,
        result={"content": "..."},
        error=None,
        started_at=100.0,
        completed_at=105.0,
        dependency_satisfied_at=99.5,
    )

    assert task.task_id == "task-1"
    assert task.category is ToolCategory.FETCH
    assert task.tool_name == "jina_fetch"
    assert task.params == {"url": "https://example.gov.in/scheme"}
    assert task.priority == 3
    assert task.timeout_ms == 9000
    assert task.depends_on == ["task-0"]
    assert task.agent_role == "Researcher_Agent"
    assert task.status is TaskStatus.ELIGIBLE
    assert task.retries_used == 1
    assert task.result == {"content": "..."}
    assert task.error is None
    assert task.started_at == 100.0
    assert task.completed_at == 105.0
    assert task.dependency_satisfied_at == 99.5


# ---------------------------------------------------------------------------
# Default field values.
# ---------------------------------------------------------------------------


def test_default_field_values():
    task = _make_task()

    assert task.status is TaskStatus.PENDING
    assert task.retries_used == 0
    assert task.result is None
    assert task.error is None
    assert task.started_at is None
    assert task.completed_at is None
    assert task.dependency_satisfied_at is None


def test_depends_on_defaults_are_independent_lists():
    """Each ToolTask must get its own depends_on list, never a shared
    mutable default, even when constructed without explicitly passing one
    of the list-shaped fields.
    """
    task_a = _make_task(task_id="a")
    task_b = _make_task(task_id="b")

    task_a.depends_on.append("x")

    assert task_a.depends_on == ["x"]
    assert task_b.depends_on == []


# ---------------------------------------------------------------------------
# priority / timeout_ms boundary validation (Requirement 2.1).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("priority", [1, 5, 10])
def test_priority_within_bounds_is_accepted(priority):
    task = _make_task(priority=priority)
    assert task.priority == priority


@pytest.mark.parametrize("priority", [0, -1, 11, 100])
def test_priority_out_of_bounds_raises_value_error(priority):
    with pytest.raises(ValueError):
        _make_task(priority=priority)


@pytest.mark.parametrize("timeout_ms", [1, 100, 30000])
def test_timeout_ms_positive_is_accepted(timeout_ms):
    task = _make_task(timeout_ms=timeout_ms)
    assert task.timeout_ms == timeout_ms


@pytest.mark.parametrize("timeout_ms", [0, -1, -100])
def test_timeout_ms_non_positive_raises_value_error(timeout_ms):
    with pytest.raises(ValueError):
        _make_task(timeout_ms=timeout_ms)
