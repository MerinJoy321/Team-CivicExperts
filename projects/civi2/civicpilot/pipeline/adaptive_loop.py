"""Adaptive evidence-sufficiency loop (Requirements 13.1-13.5).

Assesses evidence sufficiency and executes targeted gap-fill operations,
capped at a maximum of 5 cycles per determination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from civicpilot.agents.models import EligibilityCriterion, SchemeCandidate


@dataclass
class SufficiencyReport:
    """Report detailing evidence sufficiency status."""

    is_sufficient: bool
    unresolved_criteria: list[str] = field(default_factory=list)
    skipped_operations: list[str] = field(default_factory=list)
    cycles_used: int = 0


def assess_sufficiency(criteria: list[EligibilityCriterion]) -> SufficiencyReport:
    """Requirement 13.1 / Property 18: Checks whether evidence is sufficient.

    Sufficient when every applicable criterion is resolved (PASS or FAIL)
    or if any FAIL is already present (which deterministically fixes outcome to NOT_ELIGIBLE).
    """
    unresolved = [c.criterion_id for c in criteria if c.classification == "UNKNOWN"]
    has_fail = any(c.classification == "FAIL" for c in criteria)

    # If any criterion is FAIL, overall is NOT_ELIGIBLE regardless of UNKNOWNs -> sufficient
    if has_fail or len(unresolved) == 0:
        return SufficiencyReport(is_sufficient=True, unresolved_criteria=[])

    return SufficiencyReport(is_sufficient=False, unresolved_criteria=unresolved)


class AdaptiveEvidenceLoop:
    """Executes targeted gap-fill operations bounded to 5 cycles maximum."""

    MAX_CYCLES = 5

    def __init__(self, fetch_gap_fill_fn: Optional[Callable[[str], Any]] = None) -> None:
        self._fetch_gap_fill_fn = fetch_gap_fill_fn

    async def run_loop(
        self,
        candidate: SchemeCandidate,
        criteria: list[EligibilityCriterion],
        evaluator_fn: Callable[[EligibilityCriterion], Any],
    ) -> tuple[list[EligibilityCriterion], SufficiencyReport]:
        """Runs adaptive evidence cycle up to 5 times per determination.

        Requirement 13.2-13.5 / Property 18: Bounded loop (<=5 cycles) monotonically resolving criteria.
        """
        current_criteria = list(criteria)
        cycles = 0
        skipped_ops: list[str] = []

        while cycles < self.MAX_CYCLES:
            cycles += 1
            report = assess_sufficiency(current_criteria)

            if report.is_sufficient:
                report.cycles_used = cycles
                report.skipped_operations = skipped_ops
                return current_criteria, report

            # Attempt gap-fill resolution for unresolved criteria
            next_criteria: list[EligibilityCriterion] = []
            for c in current_criteria:
                if c.classification == "UNKNOWN":
                    # Attempt targeted evaluation
                    res = await evaluator_fn(c)
                    next_criteria.append(res)
                else:
                    next_criteria.append(c)

            current_criteria = next_criteria

        final_report = assess_sufficiency(current_criteria)
        final_report.cycles_used = cycles
        final_report.skipped_operations = skipped_ops
        return current_criteria, final_report
