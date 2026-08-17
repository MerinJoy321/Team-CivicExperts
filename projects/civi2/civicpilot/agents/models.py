"""Core domain data models for agents and pipeline (Requirements 7, 13, 14, 15, 16)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


@dataclass
class Profile:
    """Structured citizen attribute record."""

    age: Optional[int] = None
    gender: Optional[str] = None
    income: Optional[float] = None
    location: Optional[str] = None
    category: Optional[str] = None  # e.g., SC/ST/OBC/General
    special_status: Optional[list[str]] = None  # e.g. ["widow", "disabled"]
    family_size: Optional[int] = None
    education_level: Optional[str] = None
    occupation: Optional[str] = None
    stated_need: Optional[str] = None
    raw_input: str = ""


@dataclass
class EligibilityCriterion:
    """Single eligibility criterion evaluation."""

    criterion_id: str
    scheme_id: str
    description: str
    profile_field: Optional[str] = None
    comparator: Optional[str] = None  # "gte" | "lte" | "eq" | "in" | None
    threshold: Any = None
    classification: Literal["PASS", "FAIL", "UNKNOWN"] = "UNKNOWN"
    evidence_source_ids: list[str] = field(default_factory=list)
    resolved_via: Literal["deterministic", "reasoning_model", "unresolved"] = "unresolved"


@dataclass
class SchemeCandidate:
    """A scheme candidate discovered during research."""

    scheme_id: str
    name: str
    source_urls: list[str] = field(default_factory=list)
    priority_tier: int = 6  # 1-6 per Requirement 9.5
    identity_info_complete: bool = True
    application_info_complete: bool = True
    criteria: list[EligibilityCriterion] = field(default_factory=list)


@dataclass
class EligibilityResult:
    """Evaluated eligibility outcome for a scheme candidate."""

    scheme_id: str
    overall: Literal["ELIGIBLE", "NOT_ELIGIBLE", "POSSIBLE_NEEDS_INFO"]
    confidence_level: Literal["HIGH", "MEDIUM", "LOW"]
    criteria: list[EligibilityCriterion] = field(default_factory=list)
    skipped_operations: list[str] = field(default_factory=list)
    unresolved_criteria: list[str] = field(default_factory=list)
    degraded: bool = False


@dataclass
class DocumentOutcome:
    """Result of DocumentAdvisorAgent document generation decision."""

    generated: bool
    scheme_id: str = ""
    document_bytes: Optional[bytes] = None
    error: Optional[str] = None


@dataclass
class TraceEvent:
    """A single trace event for performance tracking and streaming UI."""

    tool_name: str
    operation_description: str
    status: Literal["RUNNING", "COMPLETE", "FAILED", "SKIPPED"]
    elapsed_s: float
    result_summary: str


@dataclass
class FinalReport:
    """Citizen-facing final synthesis report."""

    profile_summary: str
    results: list[EligibilityResult]
    scheme_candidates: list[SchemeCandidate]
    official_links: list[dict[str, str]]
    performance_trace: str
    documents: list[DocumentOutcome] = field(default_factory=list)
