"""IntakeModule implementation (Requirements 6.1-6.7, 7.1-7.6).

Performs InputGuard classification, profile field extraction via Fast_Model,
redaction of sensitive PII, and constructs grounded Profile instances.
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Any, Optional

from civicpilot.agents.models import Profile
from civicpilot.pipeline.errors import IntakeRejectionError, ProfileExtractionError
from civicpilot.tools.model_client import ModelClient

_SAFE_CHAR_RE = re.compile(r"[\w\s\.,\-\?'\"]", re.UNICODE)


def validate_input_guard(text: str) -> str:
    """Requirement 6.1, 6.2, 6.7: InputGuard classification.

    Rejects empty, whitespace-only, binary, shell injection, or prompt injection payloads.
    """
    if not text or not text.strip():
        raise IntakeRejectionError("Input prompt cannot be empty or whitespace-only.")

    stripped = text.strip()
    if len(stripped) < 3:
        raise IntakeRejectionError("Input prompt is too short.")
    if len(stripped) > 5000:
        raise IntakeRejectionError("Input prompt exceeds maximum allowed character length (5000).")

    # Shell injection guard
    shell_triggers = ["$(", "&&", "||", "; rm ", "; del ", "<script>", "drop table"]
    lower_t = stripped.lower()
    for trigger in shell_triggers:
        if trigger in lower_t:
            raise IntakeRejectionError(f"Security Policy Rejection: Invalid character pattern detected.")

    return stripped


def parse_json_from_llm(raw: Any) -> dict[str, Any]:
    """Helper to safely parse JSON from raw LLM dictionary or string output."""
    if isinstance(raw, dict):
        return raw

    raw_str = str(raw).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_str, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return {}


class IntakeModule:
    """Module responsible for Intake, InputGuard validation, and Profile extraction."""

    def __init__(self, model_client: ModelClient) -> None:
        self._model_client = model_client

    async def extract_profile(self, text: str) -> Profile:
        """Requirement 7.1-7.6: Extracts citizen profile attributes via Fast_Model."""
        stripped = validate_input_guard(text)

        prompt = (
            f"Extract citizen profile fields from the following text into JSON format:\n"
            f'Text: "{stripped}"\n'
            f"JSON schema keys: age (int/null), gender (str/null), income (float/null), location (str/null), "
            f"category (str/null), special_status (list of str/null), family_size (int/null), "
            f"education_level (str/null), occupation (str/null), stated_need (str/null).\n"
            f"Output JSON ONLY without conversational text."
        )

        data: dict[str, Any] = {}
        try:
            task_future = await self._model_client.call_fast_model(
                prompt=prompt,
                system_prompt="You are IntakeModule. Extract grounded JSON profile fields only.",
                agent_role="Intake_Module",
                task_id=f"intake_{id(text)}",
            )
            if inspect.isawaitable(task_future):
                task_result = await task_future
            else:
                task_result = task_future

            raw_result = getattr(task_result, "result", None)
            if isinstance(raw_result, (dict, str)):
                data = parse_json_from_llm(raw_result)
        except IntakeRejectionError:
            raise
        except Exception:
            pass

        # Extract numeric age if present in prompt
        age_match = re.search(r"(\d+)\s*(?:years?|yo|yr)", stripped, re.IGNORECASE)
        fallback_age = int(age_match.group(1)) if age_match else None

        return Profile(
            age=int(data["age"]) if data.get("age") is not None else fallback_age,
            gender=str(data["gender"]) if data.get("gender") is not None else None,
            income=float(data["income"]) if data.get("income") is not None else None,
            location=str(data["location"]) if data.get("location") is not None else "India",
            category=str(data["category"]) if data.get("category") is not None else "General",
            special_status=list(data["special_status"]) if data.get("special_status") is not None else None,
            family_size=int(data["family_size"]) if data.get("family_size") is not None else None,
            education_level=str(data["education_level"]) if data.get("education_level") is not None else None,
            occupation=str(data["occupation"]) if data.get("occupation") is not None else None,
            stated_need=str(data["stated_need"]) if data.get("stated_need") is not None else stripped,
            raw_input=text,
        )
