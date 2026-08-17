"""Exhaustive boundary-value and role-config tests for
civicpilot.config.settings.load_settings (Task 1.5).

Task 1.2 (tests/config/test_settings.py) already covers: default fallback
for all three concurrency variables, one valid override, one parametrized
invalid case per concurrency variable, missing-role failures (whole role
missing), and one empty-credential case. This file adds the exhaustive
sweep required by Task 1.5 without duplicating that coverage:

- Each of MAX_SEARCH_CONCURRENCY, MAX_FETCH_CONCURRENCY,
  MAX_VERIFY_CONCURRENCY individually at values 0, 1, 32, 33, and a
  non-integer string, confirming fallback-to-default + warning for the
  out-of-range/non-integer values and as-is acceptance with no warning for
  the in-range boundary values 1 and 32 (Requirement 23.4).
- Missing/invalid Fast_Model and Reasoning_Model configuration (missing,
  empty, and whitespace-only) for every individual role variable, causing
  startup failure with a role- and variable-identifying error
  (Requirement 6.4).
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

_CONCURRENCY_FIELD_BY_VAR = {
    "MAX_SEARCH_CONCURRENCY": "max_search_concurrency",
    "MAX_FETCH_CONCURRENCY": "max_fetch_concurrency",
    "MAX_VERIFY_CONCURRENCY": "max_verify_concurrency",
}

_CONCURRENCY_DEFAULTS = {
    "MAX_SEARCH_CONCURRENCY": 4,
    "MAX_FETCH_CONCURRENCY": 4,
    "MAX_VERIFY_CONCURRENCY": 3,
}


# ---------------------------------------------------------------------------
# Requirement 23.4: exhaustive boundary sweep for each concurrency variable.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("var_name", list(_CONCURRENCY_DEFAULTS))
@pytest.mark.parametrize(
    "raw_value,expect_fallback",
    [
        ("0", True),  # below the valid range [1, 32]
        ("1", False),  # lower boundary, valid
        ("32", False),  # upper boundary, valid
        ("33", True),  # above the valid range [1, 32]
        ("12.5", True),  # non-integer
    ],
)
def test_concurrency_boundary_sweep(var_name, raw_value, expect_fallback):
    """Requirement 23.4: 0, 1, 32, 33, and a non-integer value are tested
    individually for each of the three concurrency variables. Out-of-range
    or non-integer values fall back to the default with exactly one
    startup warning naming the invalid variable; the in-range boundary
    values 1 and 32 are accepted as-is with no warning.
    """
    env = dict(VALID_MODEL_ENV, **{var_name: raw_value})
    settings = load_settings(env=env)

    resolved = getattr(settings.concurrency, _CONCURRENCY_FIELD_BY_VAR[var_name])

    if expect_fallback:
        assert resolved == _CONCURRENCY_DEFAULTS[var_name]
        assert len(settings.startup_warnings) == 1
        assert var_name in settings.startup_warnings[0]
    else:
        assert resolved == int(raw_value)
        assert settings.startup_warnings == []

    # Only the variable under test was touched; the other two concurrency
    # variables must remain unaffected (still at their defaults, no warning
    # attributable to them).
    for other_var, other_default in _CONCURRENCY_DEFAULTS.items():
        if other_var == var_name:
            continue
        other_field = _CONCURRENCY_FIELD_BY_VAR[other_var]
        assert getattr(settings.concurrency, other_field) == other_default
        assert all(other_var not in w for w in settings.startup_warnings)


# ---------------------------------------------------------------------------
# Requirement 6.4: missing/invalid Fast_Model and Reasoning_Model config.
# ---------------------------------------------------------------------------

_MODEL_ROLE_VARS = [
    ("Fast_Model", "FAST_MODEL_NAME"),
    ("Fast_Model", "FAST_MODEL_ENDPOINT"),
    ("Fast_Model", "FAST_MODEL_API_KEY"),
    ("Reasoning_Model", "REASONING_MODEL_NAME"),
    ("Reasoning_Model", "REASONING_MODEL_ENDPOINT"),
    ("Reasoning_Model", "REASONING_MODEL_API_KEY"),
]


@pytest.mark.parametrize("role,var_name", _MODEL_ROLE_VARS)
@pytest.mark.parametrize(
    "invalid_value",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace-only"),
    ],
)
def test_invalid_model_role_variable_fails_startup_with_role_identifying_error(
    role, var_name, invalid_value
):
    """Requirement 6.4: for every individual Fast_Model/Reasoning_Model
    variable, a missing, empty, or whitespace-only value fails startup with
    a ConfigurationError that identifies both the failing role and the
    specific variable, without touching the other (valid) role's config.
    """
    env = dict(VALID_MODEL_ENV)
    if invalid_value is None:
        del env[var_name]
    else:
        env[var_name] = invalid_value

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env=env)

    message = str(exc_info.value)
    assert role in message
    assert var_name in message

    other_role = "Reasoning_Model" if role == "Fast_Model" else "Fast_Model"
    assert other_role not in message
