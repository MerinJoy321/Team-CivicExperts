"""ReporterAgent implementation (Requirements 4.9, 16.3, 16.4).

Assembles the citizen-facing FinalReport with per-scheme result, confidence,
PortalValidator-backed official portal link annotations, and a readable performance trace.
"""

from __future__ import annotations

from typing import Any, Optional

from civicpilot.agents.models import FinalReport, Profile, SchemeCandidate
from civicpilot.telemetry.telemetry_module import TelemetryModule
from civicpilot.tools.portal_validator import PortalValidator


class ReporterAgent:
    """CrewAI Reporter_Agent role wrapper.

    Synthesizes eligibility results and performance traces into a FinalReport.
    """

    def __init__(
        self,
        telemetry: Optional[TelemetryModule] = None,
        portal_validator: Optional[PortalValidator] = None,
    ) -> None:
        self._telemetry = telemetry
        self._portal_validator = portal_validator or PortalValidator()

    def format_performance_trace(self, snapshot: Any = None) -> str:
        """Requirement 4.9: Formats performance trace as readable text.

        Includes tool name, PARALLEL/SEQUENTIAL mode, completed/total counts, and elapsed seconds.
        """
        lines = ["=== Performance Trace ==="]

        if snapshot is not None and hasattr(snapshot, "trace_events") and snapshot.trace_events:
            for ev in snapshot.trace_events:
                tool = getattr(ev, "tool_name", "tool")
                mode = "PARALLEL" if getattr(ev, "batch_size", 1) > 1 else "SEQUENTIAL"
                elapsed = getattr(ev, "elapsed_s", 0.0)
                status = getattr(ev, "status", "COMPLETE")
                lines.append(
                    f"Tool: {tool} | Mode: {mode} | Status: {status} | Elapsed: {elapsed:.3f}s"
                )
        elif self._telemetry is not None:
            batches = self._telemetry.batches()
            for b in batches:
                mode = "PARALLEL" if b.batch_size > 1 else "SEQUENTIAL"
                lines.append(
                    f"Tool: {b.category.value} | Mode: {mode} | Batch Size: {b.batch_size} | Elapsed: {b.elapsed_s:.3f}s"
                )
        else:
            lines.append("No trace events recorded.")

        return "\n".join(lines)

    def synthesize(
        self,
        profile: Profile,
        candidates: list[SchemeCandidate],
        results: list[Any],
        snapshot: Any = None,
    ) -> FinalReport:
        """Requirement 16.3, 16.4: Annotates official links via PortalValidator and builds FinalReport."""
        official_links: list[dict[str, str]] = []

        for candidate in candidates:
            verified_urls: list[str] = []
            if candidate.source_urls:
                for u in candidate.source_urls:
                    v_url = self._portal_validator.get_verified_portal_url(u, candidate.name)
                    if v_url not in verified_urls:
                        verified_urls.append(v_url)
            else:
                verified_urls.append(self._portal_validator.get_verified_portal_url("", candidate.name))

            candidate.source_urls = verified_urls
            for url in candidate.source_urls:
                is_official = self._portal_validator.is_official(url)
                official_links.append(
                    {
                        "scheme_name": candidate.name,
                        "url": url,
                        "is_official": "Official Portal" if is_official else "Non-Official Source",
                    }
                )

        trace_text = self.format_performance_trace(snapshot)
        summary_str = f"Eligibility report generated for profile: {profile.occupation or 'Citizen'}"

        return FinalReport(
            profile_summary=summary_str,
            results=results,
            scheme_candidates=candidates,
            official_links=official_links,
            performance_trace=trace_text,
        )

    def generate_report(
        self,
        profile: Profile,
        candidates: list[SchemeCandidate],
        results: list[Any],
        snapshot: Any = None,
    ) -> FinalReport:
        """Alias method for synthesize."""
        return self.synthesize(profile, candidates, results, snapshot)
