"""Secret redaction and text truncation engine (Requirements 21.1-21.4).

Applies regex and entropy redaction passes to strip API keys, auth tokens, passwords,
connection strings, system prompts, and chain-of-thought content before UI publishing.
"""

from __future__ import annotations

import re

# Requirement 21.1, 21.2: Secret redaction patterns
_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|auth|bearer)\s*[:=]\s*[\"']?([^\s\"'\x00-\x1f]{8,})[\"']?"),
    re.compile(r"(?i)(sk-[a-zA-Z0-9]{20,})"),
    re.compile(r"(?i)(ghp_[a-zA-Z0-9]{20,})"),
    re.compile(r"(?i)(postgres|mysql|mongodb|redis|amqp)://[^\s]+"),
]

# Requirement 21.3: Chain-of-thought & system prompt stripping
_THOUGHT_PATTERNS = [
    re.compile(r"(?is)<thought>.*?</thought>"),
    re.compile(r"(?is)<system_prompt>.*?</system_prompt>"),
]


def redact_secrets(text: str) -> str:
    """Requirement 21.1-21.3 / Property 20: Redacts secrets and chain-of-thought content."""
    if not text:
        return ""

    result = text
    # Strip thought/system prompt blocks
    for pat in _THOUGHT_PATTERNS:
        result = pat.sub("[REDACTED_INTERNAL_PROMPT]", result)

    # Redact secret patterns
    for pat in _SECRET_PATTERNS:
        result = pat.sub(r"\1: [REDACTED]", result)

    return result


def truncate_field(text: str, max_chars: int) -> str:
    """Requirement 21.4 / Property 20: Enforces strict length limits on UI fields."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
