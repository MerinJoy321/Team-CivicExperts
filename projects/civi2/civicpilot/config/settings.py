"""Environment configuration loader (Requirements 6.3-6.5, 6.8, 23.1-23.6).

This module loads two independent groups of startup configuration:

1. Scheduler concurrency limits -- ``MAX_SEARCH_CONCURRENCY``,
   ``MAX_FETCH_CONCURRENCY``, ``MAX_VERIFY_CONCURRENCY``. An unset variable
   uses its documented default (4, 4, 3 respectively). A variable set to a
   value that is not an integer in ``[1, 32]`` is rejected: the loader falls
   back to that variable's default and records a startup warning naming the
   invalid variable, but the System still starts (Requirement 23.4).

2. Fast_Model / Reasoning_Model provider configuration -- a model
   identifier, provider endpoint, and auth credential for each of the two
   model roles. Naming convention chosen for this project:
   ``FAST_MODEL_NAME`` / ``FAST_MODEL_ENDPOINT`` / ``FAST_MODEL_API_KEY`` and
   ``REASONING_MODEL_NAME`` / ``REASONING_MODEL_ENDPOINT`` /
   ``REASONING_MODEL_API_KEY``. If either role's configuration is missing or
   invalid, startup fails with a ``ConfigurationError`` that identifies which
   role failed (Requirement 6.4) rather than silently defaulting -- unlike
   the concurrency variables, there is no safe default for "which LLM to
   call", so failing fast is the correct behavior here.

Values are read from the process environment, which is first populated from
a ``.env`` file (if present) via ``python-dotenv``. Callers that need
deterministic, isolated behavior (tests) can pass an explicit ``env``
mapping to :func:`load_settings` instead of relying on process environment
variables.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Requirement 23.4: valid concurrency values are integers in [1, 32] inclusive.
_CONCURRENCY_MIN = 1
_CONCURRENCY_MAX = 32

# Requirement 23.1-23.3: env var name -> documented default.
_CONCURRENCY_SPECS: dict[str, int] = {
    "MAX_SEARCH_CONCURRENCY": 4,
    "MAX_FETCH_CONCURRENCY": 4,
    "MAX_VERIFY_CONCURRENCY": 3,
}

# Requirement 6.3, 6.8: env var names for each model role's identifier,
# provider endpoint, and auth credential.
_MODEL_ROLE_VARS: dict[str, tuple[str, str, str]] = {
    "Fast_Model": ("FAST_MODEL_NAME", "FAST_MODEL_ENDPOINT", "FAST_MODEL_API_KEY"),
    "Reasoning_Model": (
        "REASONING_MODEL_NAME",
        "REASONING_MODEL_ENDPOINT",
        "REASONING_MODEL_API_KEY",
    ),
}


class ConfigurationError(Exception):
    """Raised at startup when a model role's provider configuration is
    missing or invalid (Requirement 6.4). The message always identifies the
    failing role and which variable(s) were the problem; it never echoes a
    credential value.
    """


@dataclass(frozen=True)
class ModelConfig:
    """Provider configuration for a single model role (Fast_Model or
    Reasoning_Model): a model identifier plus connection parameters.
    """

    name: str
    endpoint: str
    api_key: str


@dataclass(frozen=True)
class ConcurrencyConfig:
    """Resolved per-category concurrency limits (Requirement 23.1-23.5)."""

    max_search_concurrency: int
    max_fetch_concurrency: int
    max_verify_concurrency: int


@dataclass(frozen=True)
class Settings:
    """Top-level resolved startup configuration."""

    concurrency: ConcurrencyConfig
    fast_model: ModelConfig
    reasoning_model: ModelConfig
    startup_warnings: list[str] = field(default_factory=list)


def _parse_concurrency_value(
    var_name: str, default: int, raw_value: Optional[str], warnings: list[str]
) -> int:
    """Resolves a single concurrency variable, recording a startup warning
    and falling back to ``default`` if the raw value is unset, non-integer,
    or outside [1, 32] (Requirement 23.4).
    """
    if raw_value is None:
        return default

    stripped = raw_value.strip()
    try:
        # int() rejects non-integer strings such as "4.0", "abc", "" outright,
        # which is exactly the "reject non-integer values" behavior required.
        value = int(stripped)
    except ValueError:
        message = (
            f"Invalid value for {var_name}={raw_value!r}: not an integer. "
            f"Falling back to default {default}."
        )
        warnings.append(message)
        logger.warning(message)
        return default

    if value < _CONCURRENCY_MIN or value > _CONCURRENCY_MAX:
        message = (
            f"Invalid value for {var_name}={value}: must be an integer in "
            f"[{_CONCURRENCY_MIN}, {_CONCURRENCY_MAX}]. Falling back to default {default}."
        )
        warnings.append(message)
        logger.warning(message)
        return default

    return value


def _load_concurrency_config(
    env: Mapping[str, str], warnings: list[str]
) -> ConcurrencyConfig:
    resolved = {
        var_name: _parse_concurrency_value(var_name, default, env.get(var_name), warnings)
        for var_name, default in _CONCURRENCY_SPECS.items()
    }
    return ConcurrencyConfig(
        max_search_concurrency=resolved["MAX_SEARCH_CONCURRENCY"],
        max_fetch_concurrency=resolved["MAX_FETCH_CONCURRENCY"],
        max_verify_concurrency=resolved["MAX_VERIFY_CONCURRENCY"],
    )


def _load_model_config(role: str, env: Mapping[str, str]) -> ModelConfig:
    name_var, endpoint_var, api_key_var = _MODEL_ROLE_VARS[role]
    name = (env.get(name_var) or "").strip()
    endpoint = (env.get(endpoint_var) or "").strip()
    api_key = (env.get(api_key_var) or "").strip()

    missing = [
        var_name
        for var_name, value in (
            (name_var, name),
            (endpoint_var, endpoint),
            (api_key_var, api_key),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError(
            f"{role} configuration is missing or invalid: environment "
            f"variable(s) {', '.join(missing)} must be set to a non-empty value."
        )

    return ModelConfig(name=name, endpoint=endpoint, api_key=api_key)


def load_settings(
    env: Optional[Mapping[str, str]] = None,
    *,
    dotenv_path: Optional[str] = None,
) -> Settings:
    """Loads and validates startup configuration.

    Args:
        env: An explicit environment mapping to read from. When omitted
            (the normal startup path), ``.env`` is loaded into the process
            environment via ``python-dotenv`` and ``os.environ`` is used.
            Tests should pass an explicit mapping for isolation.
        dotenv_path: Optional explicit path to a ``.env`` file, forwarded to
            ``python-dotenv``. Ignored when ``env`` is provided explicitly.

    Returns:
        A populated :class:`Settings` instance. ``startup_warnings`` lists
        every concurrency variable that was rejected and defaulted.

    Raises:
        ConfigurationError: if Fast_Model's or Reasoning_Model's provider
            configuration is missing or invalid (Requirement 6.4).
    """
    if env is None:
        load_dotenv(dotenv_path=dotenv_path, override=False)
        env = os.environ

    warnings: list[str] = []
    concurrency = _load_concurrency_config(env, warnings)

    # Requirement 6.4: fail startup with a role-identifying error if either
    # role's config is missing/invalid. Both roles are checked (rather than
    # raising on the first failure) so a single startup failure message can,
    # in principle, name either role -- callers that want to report both
    # failures at once can catch and inspect both roles individually, but a
    # single ConfigurationError raised here per role keeps the error message
    # unambiguous about which role failed.
    fast_model = _load_model_config("Fast_Model", env)
    reasoning_model = _load_model_config("Reasoning_Model", env)

    return Settings(
        concurrency=concurrency,
        fast_model=fast_model,
        reasoning_model=reasoning_model,
        startup_warnings=warnings,
    )
