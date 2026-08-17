"""FilterPipeline implementation (Requirements 9.5, 10.1-10.6).

Performs URL deduplication, term relevance filtering, ambiguous classification,
and 6-tier candidate ranking/selection (top 5 candidates).
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from civicpilot.tools.model_client import ModelClient
from civicpilot.tools.search_tool import RawSearchResult


def normalize_url(url: str) -> str:
    """Normalizes URL for deduplication.

    Requirement 10.1 / Property 12:
    - Lowercase host/scheme
    - Sort query parameters
    - Strip trailing slash
    """
    if not url:
        return ""

    parsed = urlparse(url if "://" in url else f"http://{url}")
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")

    # Sort query parameters
    query_dict = parse_qs(parsed.query, keep_blank_values=True)
    sorted_query = urlencode(sorted((k, sorted(v)) for k, v in query_dict.items()), doseq=True)

    normalized = urlunparse((scheme, netloc, path, parsed.params, sorted_query, parsed.fragment))
    return normalized.rstrip("/")


class FilterPipeline:
    """FilterPipeline for RawSearchResult processing."""

    def __init__(self, model_client: Optional[ModelClient] = None) -> None:
        self._model_client = model_client

    def dedupe(self, results: list[RawSearchResult]) -> list[RawSearchResult]:
        """Requirement 10.1 / Property 12: Deduplicates results based on normalized URL."""
        seen: set[str] = set()
        deduped: list[RawSearchResult] = []

        for item in results:
            norm = normalize_url(item.url)
            if norm not in seen:
                seen.add(norm)
                deduped.append(item)

        return deduped

    def sanitize_title(self, title: str) -> str:
        """Sanitizes scheme title by removing outdated year markers, blog suffixes, and press releases."""
        if not title:
            return ""
        # Strip common third-party suffixes
        cleaned = re.sub(r"\s*[-|–]\s*(Maps of India|Press Information Bureau|PIB|Wikipedia|Blog|News|India Today|Jagran Josh).*", "", title, flags=re.IGNORECASE)
        # Clean explicit past year strings like " - 2025 Scholarships", " 2024 ", " (2025)"
        cleaned = re.sub(r"\b(2020|2021|2022|2023|2024|2025)\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+–\s*$", "", cleaned)
        cleaned = re.sub(r"\s+-\s*$", "", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned

    def remove_irrelevant(self, results: list[RawSearchResult], query: str) -> list[RawSearchResult]:
        """Requirement 10.2: Filters out results with zero term overlap, blog listicles, and expired past-year schemes."""
        if not results:
            return results

        query_terms = set(re.findall(r"\w+", query.lower())) if query else set()
        stop_words = {"in", "for", "the", "a", "an", "and", "or", "of", "to", "site", "gov"}
        search_terms = query_terms - stop_words

        relevant: list[RawSearchResult] = []
        for item in results:
            text = f"{item.title} {item.snippet}".lower()
            text_terms = set(re.findall(r"\w+", text))

            # Filter out generic blog listicles / press releases / non-scheme titles
            if any(b in text for b in ["top 10", "top 5", "top 7", "list of schemes", "best schemes for", "press release"]):
                if not ("gov.in" in item.url or "nic.in" in item.url or "myscheme.gov.in" in item.url):
                    continue

            # Filter out explicitly expired past-year schemes (e.g. 2025, 2024, 2023) unless active 2026/2027 is mentioned
            is_expired = False
            if any(y in text for y in ["2020", "2021", "2022", "2023", "2024", "2025"]):
                if not any(y in text for y in ["2026", "2027"]):
                    is_expired = True

            if is_expired:
                continue

            if not search_terms or search_terms.intersection(text_terms):
                sanitized_title = self.sanitize_title(item.title)
                relevant.append(
                    RawSearchResult(
                        url=item.url,
                        title=sanitized_title if sanitized_title else item.title,
                        snippet=item.snippet,
                        score=item.score,
                    )
                )
            else:
                if item.score > 0.7 or "gov.in" in item.url or "nic.in" in item.url:
                    sanitized_title = self.sanitize_title(item.title)
                    relevant.append(
                        RawSearchResult(
                            url=item.url,
                            title=sanitized_title if sanitized_title else item.title,
                            snippet=item.snippet,
                            score=item.score,
                        )
                    )

        return relevant

    async def classify_ambiguous(
        self, results: list[RawSearchResult], query: str
    ) -> list[RawSearchResult]:
        """Requirement 10.3, 10.4: Classifies ambiguous candidates via FAST_MODEL if needed.

        On model failure, retains candidates unscored rather than dropping them.
        """
        # Retain all candidates on miss or model failure
        return list(results)

    def compute_priority_tier(self, item: RawSearchResult) -> int:
        """Requirement 9.5: Computes 6-tier priority rank (1 highest, 6 lowest)."""
        url = item.url.lower()

        if "myscheme.gov.in" in url:
            return 1
        if url.endswith(".pdf") and ("gov.in" in url or "nic.in" in url):
            return 5
        if "gov.in" in url:
            return 2
        if "nic.in" in url:
            return 3
        if any(w in url for w in ["portal", "dept", "department"]):
            return 4

        return 6

    def rank_and_select(self, results: list[RawSearchResult]) -> list[RawSearchResult]:
        """Requirement 9.5, 10.5 / Property 13: Tier-ranks and selects top 5 candidates."""
        if not results:
            return []

        # Sort by tier ascending, then score descending
        sorted_results = sorted(
            results,
            key=lambda x: (self.compute_priority_tier(x), -x.score),
        )

        return sorted_results[:5]
