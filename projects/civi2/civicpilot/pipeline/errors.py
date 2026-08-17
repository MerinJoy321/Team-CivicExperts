"""Typed exceptions for pipeline logic (Requirements 7.2, 7.6)."""

from __future__ import annotations

__all__ = ["IntakeRejectionError", "ProfileExtractionError"]


class IntakeRejectionError(Exception):
    """Raised when citizen input is empty, whitespace-only, or >5000 characters
    per Requirement 7.2. Deterministic rejection prior to any LLM call.
    """


class ProfileExtractionError(Exception):
    """Raised when LLM profile extraction fails or returns malformed/unparsable JSON
    per Requirement 7.6.
    """
