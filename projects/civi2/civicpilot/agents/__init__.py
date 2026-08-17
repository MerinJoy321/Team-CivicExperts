"""CrewAI Agent roles and Architecture Guard (Requirements 1, 8, 14, 15, 16)."""

from civicpilot.agents.document_advisor_agent import DocumentAdvisorAgent
from civicpilot.agents.errors import ArchitectureViolationError, PlanningFailure
from civicpilot.agents.models import (
    DocumentOutcome,
    EligibilityCriterion,
    EligibilityResult,
    FinalReport,
    Profile,
    SchemeCandidate,
)
from civicpilot.agents.planner_agent import PlannerAgent, SearchOperation, SearchPlan
from civicpilot.agents.reporter_agent import ReporterAgent
from civicpilot.agents.researcher_agent import ResearcherAgent
from civicpilot.agents.scheduler_proxy import (
    SchedulerToolProxy,
    active_agent_context,
    get_current_agent_role,
)
from civicpilot.agents.verifier_agent import VerifierAgent

__all__ = [
    "PlannerAgent",
    "SearchPlan",
    "SearchOperation",
    "ResearcherAgent",
    "VerifierAgent",
    "DocumentAdvisorAgent",
    "ReporterAgent",
    "SchedulerToolProxy",
    "active_agent_context",
    "get_current_agent_role",
    "PlanningFailure",
    "ArchitectureViolationError",
    "Profile",
    "EligibilityCriterion",
    "SchemeCandidate",
    "EligibilityResult",
    "DocumentOutcome",
    "FinalReport",
]
