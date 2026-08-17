"""Unit tests for civicpilot.config.settings.load_settings.

These are lightweight example tests covering the core behaviors required by
Task 1.2: default fallback for the concurrency variables, invalid-value
fallback with a recorded startup warning, and startup failure when
Fast_Model/Reasoning_Model provider configuration is missing or invalid. The
exhaustive boundary-value sweep (0, 1, 32, 33, non-integer for each of the
three concurrency variables) is covered separately by Task 1.5.
"""

from __future__ import annotations

import pytest

from civicpilot.config.settings import ConfigurationError, load_settings

VALID_MODEL_ENV = {
    "FAST_MODEL_NAME": "fast-model-x",
    "FAST_MODEL_ENDPOINT": "https://api.example.com/fast",
    "FAST_MODEL_API_KEY": "fast-key-123",
    "REASONING_MODEL_NAME": "reasoning-model-y",
    "REASONING_MODEL_ENDPOINT": "https://api.example.com/reasoning",
    "REASONING_MODEL_API_KEY": "reasoning-key-456",
}


def test_concurrency_defaults_when_unset():
    """Requirement 23.1-23.3: unset concurrency vars use documented defaults."""
    settings = load_settings(env=dict(VALID_MODEL_ENV))

    assert settings.concurrency.max_search_concurrency == 4
    assert settings.concurrency.max_fetch_concurrency == 4
    assert settings.concurrency.max_verify_concurrency == 3
    assert settings.startup_warnings == []


def test_concurrency_valid_override_is_respected():
    """A valid in-range integer override should be used as-is."""
    env = dict(VALID_MODEL_ENV, MAX_SEARCH_CONCURRENCY="7")
    settings = load_settings(env=env)

    assert settings.concurrency.max_search_concurrency == 7
    assert settings.startup_warnings == []


@pytest.mark.parametrize(
    "var_name,invalid_value",
    [
        ("MAX_SEARCH_CONCURRENCY", "not-a-number"),
        ("MAX_FETCH_CONCURRENCY", "0"),
        ("MAX_VERIFY_CONCURRENCY", "33"),
    ],
)
def test_invalid_concurrency_value_falls_back_with_warning(var_name, invalid_value):
    """Requirement 23.4: invalid concurrency values fall back to the default
    and record a startup warning naming the invalid variable.
    """
    env = dict(VALID_MODEL_ENV, **{var_name: invalid_value})
    settings = load_settings(env=env)

    assert len(settings.startup_warnings) == 1
    assert var_name in settings.startup_warnings[0]

    defaults = {
        "MAX_SEARCH_CONCURRENCY": 4,
        "MAX_FETCH_CONCURRENCY": 4,
        "MAX_VERIFY_CONCURRENCY": 3,
    }
    field_by_var = {
        "MAX_SEARCH_CONCURRENCY": "max_search_concurrency",
        "MAX_FETCH_CONCURRENCY": "max_fetch_concurrency",
        "MAX_VERIFY_CONCURRENCY": "max_verify_concurrency",
    }
    assert (
        getattr(settings.concurrency, field_by_var[var_name]) == defaults[var_name]
    )


def test_missing_fast_model_config_fails_startup():
    """Requirement 6.4: missing Fast_Model config fails startup with a
    role-identifying error.
    """
    env = {
        "REASONING_MODEL_NAME": "reasoning-model-y",
        "REASONING_MODEL_ENDPOINT": "https://api.example.com/reasoning",
        "REASONING_MODEL_API_KEY": "reasoning-key-456",
    }

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env=env)

    assert "Fast_Model" in str(exc_info.value)


def test_missing_reasoning_model_config_fails_startup():
    """Requirement 6.4: missing Reasoning_Model config fails startup with a
    role-identifying error.
    """
    env = {
        "FAST_MODEL_NAME": "fast-model-x",
        "FAST_MODEL_ENDPOINT": "https://api.example.com/fast",
        "FAST_MODEL_API_KEY": "fast-key-123",
    }

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env=env)

    assert "Reasoning_Model" in str(exc_info.value)


def test_empty_model_credential_treated_as_invalid():
    """An empty-string value is treated the same as missing (Requirement 6.4)."""
    env = dict(VALID_MODEL_ENV, FAST_MODEL_API_KEY="")

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env=env)

    assert "Fast_Model" in str(exc_info.value)
    assert "FAST_MODEL_API_KEY" in str(exc_info.value)


def test_valid_model_config_loaded_correctly():
    """Sanity check that valid model config values are loaded verbatim."""
    settings = load_settings(env=dict(VALID_MODEL_ENV))

    assert settings.fast_model.name == "fast-model-x"
    assert settings.fast_model.endpoint == "https://api.example.com/fast"
    assert settings.fast_model.api_key == "fast-key-123"
    assert settings.reasoning_model.name == "reasoning-model-y"
    assert settings.reasoning_model.endpoint == "https://api.example.com/reasoning"
    assert settings.reasoning_model.api_key == "reasoning-key-456"
