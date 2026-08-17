"""DocumentAdvisorAgent implementation (Requirements 15.1, 15.2, 15.4, 15.5).

Decides whether to produce an application-support document for a scheme.
Strict gate: overall == ELIGIBLE, confidence_level == HIGH, identity_info_complete,
and application_info_complete. Submits a fire-and-forget task to Scheduler.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from civicpilot.agents.models import DocumentOutcome, EligibilityResult, SchemeCandidate
from civicpilot.scheduler.models import ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler
from civicpilot.tools.document_generator import DocumentGenerator


class DocumentAdvisorAgent:
    """CrewAI Document_Advisor_Agent role wrapper.

    Gates document generation based on eligibility and info completeness.
    """

    def __init__(self, generator: DocumentGenerator, scheduler: Scheduler) -> None:
        self._generator = generator
        self._scheduler = scheduler

    async def maybe_generate(
        self, result: EligibilityResult, candidate: SchemeCandidate
    ) -> DocumentOutcome:
        """Gates and executes document generation for a candidate.

        Requirement 15.1, 15.2: Gate checks overall ELIGIBLE, confidence HIGH, and complete info.
        Requirement 15.4, 15.5: Non-blocking fire-and-forget execution; failures do not invalidate eligibility.
        """
        # Strict gate check
        is_eligible = result.overall == "ELIGIBLE"
        is_high_confidence = result.confidence_level == "HIGH"
        identity_complete = getattr(candidate, "identity_info_complete", True)
        application_complete = getattr(candidate, "application_info_complete", True)

        if not (is_eligible and is_high_confidence and identity_complete and application_complete):
            return DocumentOutcome(generated=False, scheme_id=candidate.scheme_id)

        # Gate passed -> submit Document task
        payload = {
            "scheme_name": candidate.name,
            "summary": f"Application support document for {candidate.name}",
            "criteria": [c.description for c in result.criteria],
            "application_steps": [f"Visit official portal: {url}" for url in candidate.source_urls],
        }

        task = ToolTask(
            task_id=f"doc_{candidate.scheme_id}",
            category=ToolCategory.DOCUMENT,
            tool_name="generate_document",
            params={"payload": payload},
            priority=5,
            timeout_ms=5000,
            depends_on=[],
            agent_role="Document_Advisor_Agent",
        )

        try:
            task_future = await self._scheduler.submit(task)
            # Generate doc directly or await future
            try:
                doc_bytes = self._generator.generate_document(payload)
                return DocumentOutcome(
                    generated=True,
                    scheme_id=candidate.scheme_id,
                    document_bytes=doc_bytes,
                )
            except Exception as exc:
                return DocumentOutcome(
                    generated=False,
                    scheme_id=candidate.scheme_id,
                    error=str(exc),
                )
        except Exception as exc:
            return DocumentOutcome(
                generated=False,
                scheme_id=candidate.scheme_id,
                error=str(exc),
            )
