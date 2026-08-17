"""Property-based test for civicpilot.scheduler.dependency_graph (Task 3.3).

# Feature: civicpilot, Property 5: Failed, timed-out, or cancelled
# dependencies always skip (never execute) their dependents

Covers Property 5 from the design's Correctness Properties section: for any
ToolTask B that declares A in depends_on, if A's terminal status is FAILED,
TIMED_OUT, or CANCELLED, then B's terminal status SHALL be SKIPPED and B
SHALL never transition through RUNNING. This is checked transitively (any
task that transitively depends on the failed/timed-out/cancelled task,
directly or indirectly, must end up SKIPPED), and the property is
strengthened by also asserting isolation: tasks that do NOT depend
(transitively) on the failed task are unaffected by the failure, consistent
with test_transitive_skip_propagation_does_not_affect_independent_siblings
in tests/scheduler/test_dependency_graph.py.

Validates: Requirement 2.11
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from civicpilot.scheduler import DependencyGraph, TaskStatus, ToolCategory, ToolTask
from civicpilot.scheduler.models import SKIP_PROPAGATING_STATUSES

# Terminal statuses that always propagate a SKIPPED status to dependents
# (Requirement 2.11 / design Property 5).
_PROPAGATING_STATUSES = tuple(SKIP_PROPAGATING_STATUSES)


def _make_task(task_id: str, depends_on: list[str]) -> ToolTask:
    return ToolTask(
        task_id=task_id,
        category=ToolCategory.SEARCH,
        tool_name="tavily_search",
        params={"query": f"query-for-{task_id}"},
        priority=1,
        timeout_ms=6000,
        depends_on=depends_on,
        agent_role="Researcher_Agent",
    )


@st.composite
def _chain_dag_with_one_failed_dependency(draw):
    """Generates a small linear/shallow-tree DAG of N ToolTasks (N in
    [2, 8]) where each task at index i only depends on earlier-indexed
    tasks (0 or 1 of them), guaranteeing the graph is acyclic by
    construction. Picks one task (not the last) to be the "failed
    dependency" and sets its status to FAILED, TIMED_OUT, or CANCELLED.

    Returns a tuple of:
      - tasks: dict[str, ToolTask] keyed by task_id, in index order
      - failed_id: the task_id whose status was pre-set to a
        SKIP_PROPAGATING_STATUSES member
      - failed_status: the specific status assigned to that task
    """
    n = draw(st.integers(min_value=2, max_value=8))
    task_ids = [f"t{i}" for i in range(n)]
    tasks: dict[str, ToolTask] = {}

    for i, task_id in enumerate(task_ids):
        if i == 0:
            depends_on: list[str] = []
        else:
            # Each task depends on zero or one earlier-indexed task,
            # producing a chain or shallow-tree shape -- always acyclic
            # since edges only ever point to strictly earlier indices.
            dep_index = draw(st.integers(min_value=0, max_value=i - 1))
            has_dep = draw(st.booleans())
            depends_on = [task_ids[dep_index]] if has_dep else []
        tasks[task_id] = _make_task(task_id, depends_on)

    # Choose a task to fail; excluding the last index isn't required for
    # correctness, but including any index (even the last) is fine too --
    # a failed task with no dependents just has no propagation to check.
    failed_id = draw(st.sampled_from(task_ids))
    failed_status = draw(st.sampled_from(_PROPAGATING_STATUSES))
    tasks[failed_id].status = failed_status

    return tasks, failed_id, failed_status


def _transitive_dependents(tasks: dict[str, ToolTask], root_id: str) -> set[str]:
    """Returns the set of task_ids that transitively depend (directly or
    indirectly) on `root_id`, via `depends_on` edges."""
    dependents: set[str] = set()
    changed = True
    while changed:
        changed = False
        for task_id, task in tasks.items():
            if task_id in dependents:
                continue
            if any(
                dep_id == root_id or dep_id in dependents
                for dep_id in task.depends_on
            ):
                dependents.add(task_id)
                changed = True
    return dependents


@given(dag=_chain_dag_with_one_failed_dependency())
@settings(max_examples=100)
def test_failed_dependency_always_skips_transitive_dependents(dag):
    # Feature: civicpilot, Property 5: Failed, timed-out, or cancelled
    # dependencies always skip (never execute) their dependents
    tasks, failed_id, _failed_status = dag

    graph = DependencyGraph()
    graph.add_tasks(tasks.values())
    graph.resolve()

    dependents = _transitive_dependents(tasks, failed_id)
    non_dependents = set(tasks) - dependents - {failed_id}

    for task_id in dependents:
        task = tasks[task_id]
        assert task.status is TaskStatus.SKIPPED, (
            f"{task_id} transitively depends on failed task {failed_id} "
            f"but has status {task.status!r}, expected SKIPPED"
        )
        assert task.status is not TaskStatus.RUNNING
        assert task.status is not TaskStatus.ELIGIBLE

    # Isolation: tasks that do not transitively depend on the failed task
    # are unaffected by the failure -- they are either ELIGIBLE (no
    # unresolved dependency) or still PENDING (waiting on a dependency
    # that hasn't completed yet), but never SKIPPED as a side effect of
    # a failure that has nothing to do with them.
    for task_id in non_dependents:
        task = tasks[task_id]
        assert task.status is not TaskStatus.SKIPPED, (
            f"{task_id} does not depend on failed task {failed_id} "
            f"(directly or transitively) but was unexpectedly SKIPPED"
        )
        assert task.status in (TaskStatus.ELIGIBLE, TaskStatus.PENDING)
