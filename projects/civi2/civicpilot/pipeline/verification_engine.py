"""Eligibility Verification Engine (Requirements 14.1-14.6).

Evaluates criteria deterministically against Profile fields or via Reasoning_Model escalation.
Derives overall eligibility (NOT_ELIGIBLE if any FAIL, ELIGIBLE iff all PASS, else POSSIBLE_NEEDS_INFO).
Enforces UNKNOWN safety (Property 8) and order-independent derivation (Property 9).
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Optional

from civicpilot.agents.models import EligibilityCriterion, Profile
from civicpilot.tools.model_client import ModelClient


def evaluate_deterministic_criterion(
    criterion: EligibilityCriterion, profile: Profile
) -> EligibilityCriterion:
    """Requirement 14.1, 14.3 / Property 8: Evaluates criterion deterministically if possible.

    If required field is missing/null, classifies UNKNOWN immediately -- NEVER PASS or default.
    """
    field_name = criterion.profile_field
    comparator = criterion.comparator
    threshold = criterion.threshold

    if not field_name or not comparator:
        return criterion

    profile_value = getattr(profile, field_name, None)

    # Property 8: If required profile field is null/missing, classify UNKNOWN immediately
    if profile_value is None:
        return EligibilityCriterion(
            criterion_id=criterion.criterion_id,
            scheme_id=criterion.scheme_id,
            description=criterion.description,
            profile_field=field_name,
            comparator=comparator,
            threshold=threshold,
            classification="UNKNOWN",
            evidence_source_ids=criterion.evidence_source_ids,
            resolved_via="deterministic",
        )

    try:
        passed = False
        if comparator == "gte":
            passed = float(profile_value) >= float(threshold)
        elif comparator == "lte":
            passed = float(profile_value) <= float(threshold)
        elif comparator == "gt":
            passed = float(profile_value) > float(threshold)
        elif comparator == "lt":
            passed = float(profile_value) < float(threshold)
        elif comparator == "eq":
            passed = str(profile_value).strip().lower() == str(threshold).strip().lower()
        elif comparator == "in":
            if isinstance(threshold, (list, set, tuple)):
                passed = profile_value in threshold or str(profile_value) in threshold
            elif isinstance(profile_value, (list, set, tuple)):
                passed = threshold in profile_value or str(threshold) in profile_value
            else:
                passed = str(profile_value).lower() in str(threshold).lower()

        classification = "PASS" if passed else "FAIL"

    except Exception:
        classification = "UNKNOWN"

    return EligibilityCriterion(
        criterion_id=criterion.criterion_id,
        scheme_id=criterion.scheme_id,
        description=criterion.description,
        profile_field=field_name,
        comparator=comparator,
        threshold=threshold,
        classification=classification,
        evidence_source_ids=criterion.evidence_source_ids,
        resolved_via="deterministic",
    )


def derive_overall_result(criteria: list[EligibilityCriterion]) -> str:
    """Requirement 14.2 / Property 9: Derives overall eligibility.

    - NOT_ELIGIBLE if any criterion is FAIL (takes precedence over UNKNOWN).
    - ELIGIBLE iff every criterion is PASS.
    - Else POSSIBLE_NEEDS_INFO.
    Order-independent.
    """
    if not criteria:
        return "POSSIBLE_NEEDS_INFO"

    classifications = [c.classification for c in criteria]

    if "FAIL" in classifications:
        return "NOT_ELIGIBLE"

    if all(c == "PASS" for c in classifications):
        return "ELIGIBLE"

    return "POSSIBLE_NEEDS_INFO"


class VerificationEngine:
    """Engine executing criterion evaluation and overall derivation."""

    def __init__(self, reasoning_model: ModelClient) -> None:
        self._reasoning_model = reasoning_model

    async def evaluate_criterion(
        self, criterion: EligibilityCriterion, profile: Profile
    ) -> EligibilityCriterion:
        """Evaluates criterion deterministically first; escalates to Reasoning_Model if non-deterministic."""
        # 1. Deterministic check
        if criterion.profile_field and criterion.comparator:
            return evaluate_deterministic_criterion(criterion, profile)

        # 2. Reasoning_Model escalation
        prompt = (
            f"Evaluate criterion: '{criterion.description}' against profile: {profile}\n"
            f'Output JSON format: {{"classification": "PASS" | "FAIL" | "UNKNOWN", "reasoning": "..."}}'
        )

        try:
            task_fut = await self._reasoning_model.call_reasoning_model(
                prompt=prompt,
                system_prompt="You are VerifierAgent. Classify eligibility criterion accurately.",
                agent_role="Verifier_Agent",
                task_id=f"verify_{criterion.criterion_id}",
            )
            if inspect.isawaitable(task_fut):
                task_res = await task_fut
            else:
                task_res = task_fut

            raw_res = getattr(task_res, "result", None)
            if isinstance(raw_res, dict):
                data = raw_res
            elif isinstance(raw_res, str):
                data = json.loads(raw_res)
            else:
                data = {}

            cls_val = str(data.get("classification", "UNKNOWN")).upper()
            if cls_val not in ("PASS", "FAIL", "UNKNOWN"):
                cls_val = "UNKNOWN"

            return EligibilityCriterion(
                criterion_id=criterion.criterion_id,
                scheme_id=criterion.scheme_id,
                description=criterion.description,
                profile_field=criterion.profile_field,
                comparator=criterion.comparator,
                threshold=criterion.threshold,
                classification=cls_val,  # type: ignore[arg-type]
                evidence_source_ids=criterion.evidence_source_ids,
                resolved_via="reasoning_model",
            )
        except Exception:
            return EligibilityCriterion(
                criterion_id=criterion.criterion_id,
                scheme_id=criterion.scheme_id,
                description=criterion.description,
                profile_field=criterion.profile_field,
                comparator=criterion.comparator,
                threshold=criterion.threshold,
                classification="UNKNOWN",
                evidence_source_ids=criterion.evidence_source_ids,
                resolved_via="unresolved",
            )
