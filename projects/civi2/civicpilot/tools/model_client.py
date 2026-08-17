"""ModelClient abstraction and provider config wiring (Requirements 6.1, 6.2, 6.6-6.8, 18.4, 18.5).

Routes Fast_Model and Reasoning_Model LLM invocations through the Scheduler.
Fast_Model calls run as FAST_MODEL category tasks with a 2-4s timeout (default 3000ms).
Reasoning_Model calls run as REASONING_MODEL category tasks with a 15-30s timeout (default 25000ms).
"""

from __future__ import annotations

from typing import Any, Optional, Union

from civicpilot.config.settings import ModelConfig, Settings
from civicpilot.scheduler.models import ToolCategory, ToolTask
from civicpilot.scheduler.scheduler import Scheduler

DEFAULT_FAST_MODEL_TIMEOUT_MS = 3000  # 3 seconds (2-4s range per Requirement 18.4)
DEFAULT_REASONING_MODEL_TIMEOUT_MS = 25000  # 25 seconds (15-30s range per Requirement 18.5)


class ModelClient:
    """Provider-agnostic client interface for Fast_Model and Reasoning_Model execution."""

    def __init__(
        self,
        settings: Settings,
        scheduler: Scheduler,
        llm_executor: Optional[Any] = None,
        fast_timeout_ms: int = DEFAULT_FAST_MODEL_TIMEOUT_MS,
        reasoning_timeout_ms: int = DEFAULT_REASONING_MODEL_TIMEOUT_MS,
    ) -> None:
        self._fast_config: ModelConfig = settings.fast_model
        self._reasoning_config: ModelConfig = settings.reasoning_model
        self._scheduler = scheduler
        self._llm_executor = llm_executor
        self._fast_timeout_ms = fast_timeout_ms
        self._reasoning_timeout_ms = reasoning_timeout_ms

    @property
    def fast_model_config(self) -> ModelConfig:
        return self._fast_config

    @property
    def reasoning_model_config(self) -> ModelConfig:
        return self._reasoning_config

    async def call_fast_model(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        schema: Optional[Any] = None,
        priority: int = 5,
        depends_on: Optional[list[str]] = None,
        agent_role: str = "Agent",
        task_id: Optional[str] = None,
    ) -> ToolTask:
        """Submits a Fast_Model task to the Scheduler."""
        tid = task_id or f"fast_model_{id(prompt)}"
        params = {
            "model_name": self._fast_config.name,
            "endpoint": self._fast_config.endpoint,
            "prompt": prompt,
            "system_prompt": system_prompt,
            "schema": schema,
        }

        task = ToolTask(
            task_id=tid,
            category=ToolCategory.FAST_MODEL,
            tool_name="fast_model_llm",
            params=params,
            priority=priority,
            timeout_ms=self._fast_timeout_ms,
            depends_on=depends_on or [],
            agent_role=agent_role,
        )

        return await self._scheduler.submit(task)

    async def call_reasoning_model(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        priority: int = 5,
        depends_on: Optional[list[str]] = None,
        agent_role: str = "Agent",
        task_id: Optional[str] = None,
    ) -> ToolTask:
        """Submits a Reasoning_Model task to the Scheduler."""
        tid = task_id or f"reasoning_model_{id(prompt)}"
        params = {
            "model_name": self._reasoning_config.name,
            "endpoint": self._reasoning_config.endpoint,
            "prompt": prompt,
            "system_prompt": system_prompt,
        }

        task = ToolTask(
            task_id=tid,
            category=ToolCategory.REASONING_MODEL,
            tool_name="reasoning_model_llm",
            params=params,
            priority=priority,
            timeout_ms=self._reasoning_timeout_ms,
            depends_on=depends_on or [],
            agent_role=agent_role,
        )

        return await self._scheduler.submit(task)
