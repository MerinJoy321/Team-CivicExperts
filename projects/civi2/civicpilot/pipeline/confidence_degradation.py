"""Failure isolation and confidence degradation (Requirements 19.3, 19.4).

Downgrades result confidence level (`degraded=True`) when contributing tool/source failures occur.
Property 19: Confidence is never higher than an equivalent failure-free run.
"""

from __future__ import annotations

from typing import Literal

from civicpilot.agents.models import EligibilityResult


def apply_confidence_degradation(
    result: EligibilityResult, has_tool_failures: bool = False
) -> EligibilityResult:
    """Requirement 19.3, 19.4 / Property 19: Applies degraded confidence calculation.

    If tool/source failures occurred:
    - Sets degraded = True
    - Downgrades confidence_level: HIGH -> MEDIUM, MEDIUM -> LOW, LOW -> LOW.
    """
    if not has_tool_failures:
        return result

    # Downgrade confidence tier
    current_conf = result.confidence_level
    new_conf: Literal["HIGH", "MEDIUM", "LOW"] = "LOW"

    if current_conf == "HIGH":
        new_conf = "MEDIUM"
    elif current_conf == "MEDIUM":
        new_conf = "LOW"
    else:
        new_conf = "LOW"

    return EligibilityResult(
        scheme_id=result.scheme_id,
        overall=result.overall,
        confidence_level=new_conf,
        criteria=result.criteria,
        skipped_operations=result.skipped_operations,
        unresolved_criteria=result.unresolved_criteria,
        degraded=True,
    )
