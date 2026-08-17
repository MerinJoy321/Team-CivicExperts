"""Pipeline module: Intake, FilterPipeline, Verification Engine, Adaptive Evidence Loop, and Orchestrator."""

from civicpilot.pipeline.adaptive_loop import AdaptiveEvidenceLoop, SufficiencyReport, assess_sufficiency
from civicpilot.pipeline.confidence_degradation import apply_confidence_degradation
from civicpilot.pipeline.errors import IntakeRejectionError, ProfileExtractionError
from civicpilot.pipeline.filter_pipeline import FilterPipeline, normalize_url
from civicpilot.pipeline.intake_module import IntakeModule
from civicpilot.pipeline.orchestrator import CivicPilotPipeline
from civicpilot.pipeline.verification_engine import (
    VerificationEngine,
    derive_overall_result,
    evaluate_deterministic_criterion,
)

__all__ = [
    "IntakeModule",
    "IntakeRejectionError",
    "ProfileExtractionError",
    "FilterPipeline",
    "normalize_url",
    "VerificationEngine",
    "evaluate_deterministic_criterion",
    "derive_overall_result",
    "AdaptiveEvidenceLoop",
    "assess_sufficiency",
    "SufficiencyReport",
    "apply_confidence_degradation",
    "CivicPilotPipeline",
]
