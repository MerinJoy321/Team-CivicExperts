"""PlannerAgent implementation (Requirements 8.1-8.4).

Turns a citizen profile into a SearchPlan of 3-5 distinct SearchOperations plus
dependency annotations. Issues exactly one Reasoning_Model ToolTask per plan.
Discards and raises PlanningFailure on any violation.
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from civicpilot.agents.errors import PlanningFailure
from civicpilot.tools.model_client import ModelClient


def parse_json_from_llm(raw_text: Union[str, dict[str, Any]]) -> dict[str, Any]:
    """Robust JSON parsing extracting `{...}` substring from LLM string outputs."""
    if isinstance(raw_text, dict):
        return raw_text

    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("Empty or non-string output")

    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Extract balanced outer JSON object {...}
    start_idx = cleaned.find("{")
    if start_idx != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start_idx, len(cleaned)):
            char = cleaned[i]
            if char == '"' and not escape:
                in_string = not in_string
            elif char == '\\' and in_string and not escape:
                escape = True
                continue
            elif not in_string:
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = cleaned[start_idx : i + 1]
                        try:
                            return json.loads(candidate)
                        except Exception:
                            break
            escape = False

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        return json.loads(match.group(0))

    return json.loads(cleaned)


@dataclass(frozen=True)
class SearchOperation:
    """A single search query operation in a SearchPlan."""

    op_id: str
    query: str
    depends_on: list[str] = field(default_factory=list)


@dataclass
class SearchPlan:
    """A validated set of 3-5 distinct search operations produced by PlannerAgent."""

    operations: list[SearchOperation]
    reasoning: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validates count (3-5) and pairwise distinctness per Requirements 8.1, 8.3."""
        if not (3 <= len(self.operations) <= 5):
            raise PlanningFailure(
                f"SearchPlan must contain between 3 and 5 operations, got {len(self.operations)}"
            )

        queries_normalized = [op.query.strip().lower() for op in self.operations]
        if len(set(queries_normalized)) != len(queries_normalized):
            raise PlanningFailure("SearchPlan queries must be pairwise distinct")


class PlannerAgent:
    """CrewAI Planner_Agent role wrapper.

    Responsible for turning a structured Profile into a SearchPlan.
    """

    def __init__(self, model_client: ModelClient) -> None:
        self._model_client = model_client

    async def plan(self, profile: Any) -> SearchPlan:
        """Issues exactly one Reasoning_Model call to generate a SearchPlan.

        Requirement 8.2: Exactly one Reasoning_Model call.
        Requirement 8.1, 8.3: Validates 3-5 distinct operations or raises PlanningFailure.
        """
        prompt = (
            f"Analyze the citizen profile and output a JSON search plan containing between 3 and 5 distinct search queries.\n"
            f"IMPORTANT: The current year is 2026. Target ACTIVE 2026/2027 government schemes. Never include expired past years (such as 2025, 2024, 2023) in queries.\n"
            f"Profile: {profile}\n"
            f'Output format JSON: {{"reasoning": "...", "operations": [{{"op_id": "op1", "query": "...", "depends_on": []}}]}}\n'
            f"Output valid JSON ONLY without markdown."
        )

        try:
            task_future = await self._model_client.call_reasoning_model(
                prompt=prompt,
                system_prompt="You are CivicPilot Planner_Agent. Current year is 2026. Target active 2026/2027 official schemes only. NEVER invent or hallucinate non-existent scheme titles or acronyms. Output valid JSON only.",
                agent_role="Planner_Agent",
                task_id="planner_operation",
            )
            if inspect.isawaitable(task_future):
                task_result = await task_future
            else:
                task_result = task_future

            raw_result = getattr(task_result, "result", None)
            if raw_result is None or getattr(task_result, "status", None) != "COMPLETED":
                if isinstance(raw_result, (dict, str)):
                    parsed_json = parse_json_from_llm(raw_result)
                else:
                    raise PlanningFailure("Planning call failed or returned empty result")
            elif isinstance(raw_result, (dict, str)):
                parsed_json = parse_json_from_llm(raw_result)
            else:
                raise PlanningFailure("Invalid planning output format")

            ops_data = parsed_json.get("operations", [])
            operations = [
                SearchOperation(
                    op_id=str(op.get("op_id", f"op{idx}")),
                    query=str(op.get("query", "")),
                    depends_on=list(op.get("depends_on", [])),
                )
                for idx, op in enumerate(ops_data, start=1)
            ]
            reasoning = str(parsed_json.get("reasoning", ""))

            plan = SearchPlan(operations=operations, reasoning=reasoning)
            return plan

        except PlanningFailure:
            raise
        except Exception as exc:
            raise PlanningFailure(f"Planning failed: {exc}") from exc
