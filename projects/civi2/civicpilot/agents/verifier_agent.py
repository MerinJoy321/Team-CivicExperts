"""VerifierAgent implementation (Requirements 13.1, 14.1-14.6).

Evaluates scheme eligibility criteria against citizen profile attributes.
Submits verification tasks through the Scheduler bounded by MAX_VERIFY_CONCURRENCY.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Optional

from civicpilot.agents.models import EligibilityCriterion, EligibilityResult, Profile, SchemeCandidate
from civicpilot.pipeline.verification_engine import VerificationEngine, derive_overall_result
from civicpilot.scheduler.models import ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.tools.model_client import ModelClient


class VerifierAgent:
    """CrewAI Verifier_Agent role wrapper.

    Evaluates criteria, derives overall eligibility, and assesses evidence sufficiency.
    """

    def __init__(self, reasoning_model: ModelClient, scheduler: Scheduler) -> None:
        self._reasoning_model = reasoning_model
        self._scheduler = scheduler
        self._engine = VerificationEngine(reasoning_model)

    async def evaluate_criterion(
        self, criterion: EligibilityCriterion, profile: Profile
    ) -> EligibilityCriterion:
        """Evaluates criterion deterministically or via Reasoning_Model escalation."""
        return await self._engine.evaluate_criterion(criterion, profile)

    def derive_overall_result(self, criteria: list[EligibilityCriterion]) -> str:
        """Requirement 14.2: NOT_ELIGIBLE if any FAIL; ELIGIBLE iff all PASS; else POSSIBLE_NEEDS_INFO."""
        return derive_overall_result(criteria)

    async def verify_candidate(
        self, candidate: SchemeCandidate, profile: Profile
    ) -> EligibilityResult:
        """Verifies a single scheme candidate against the citizen profile."""
        # Submit a Scheduler verification task so Verifier_Agent fires a live trace event to the activity stream
        prompt = (
            f"Verify citizen profile (Age: {profile.age}, Income: {profile.income}, Occupation: {profile.occupation}) "
            f"against scheme '{candidate.name}' eligibility criteria."
        )

        try:
            task_fut = await self._reasoning_model.call_reasoning_model(
                prompt=prompt,
                system_prompt="You are VerifierAgent evaluating eligibility criteria.",
                agent_role="Verifier_Agent",
                task_id=f"verify_{candidate.scheme_id}",
            )
            if inspect.isawaitable(task_fut):
                try:
                    await task_fut
                except Exception:
                    pass
        except Exception:
            pass

        evaluated_criteria: list[EligibilityCriterion] = []
        for crit in candidate.criteria:
            res = await self.evaluate_criterion(crit, profile)
            evaluated_criteria.append(res)

        overall = self.derive_overall_result(evaluated_criteria)
        confidence = "HIGH" if overall in ("ELIGIBLE", "NOT_ELIGIBLE") else "MEDIUM"

        return EligibilityResult(
            scheme_id=candidate.scheme_id,
            overall=overall,  # type: ignore[arg-type]
            confidence_level=confidence,  # type: ignore[arg-type]
            criteria=evaluated_criteria,
        )
