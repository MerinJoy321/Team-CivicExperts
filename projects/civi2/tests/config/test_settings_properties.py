"""Property-based tests for civicpilot.config.settings.load_settings.

Covers Property 21 from the design's Correctness Properties section:
concurrency configuration values that are not integers in [1, 32] always
fall back to the documented default and always record a startup warning
naming the invalid variable. A contrasting property is also checked: valid
in-range integer values are always used as-is with no warning recorded.

Kept in its own file (separate from tests/config/test_settings.py) so it can
be authored/run independently of the Task 1.5 boundary-value example tests.
"""

from __future__ import annotations

import string

from hypothesis import given, settings
from hypothesis import strategies as st

from civicpilot.config.settings import load_settings

VALID_MODEL_ENV = {
    "FAST_MODEL_NAME": "fast-model-x",
    "FAST_MODEL_ENDPOINT": "https://api.example.com/fast",
    "FAST_MODEL_API_KEY": "fast-key-123",
    "REASONING_MODEL_NAME": "reasoning-model-y",
    "REASONING_MODEL_ENDPOINT": "https://api.example.com/reasoning",
    "REASONING_MODEL_API_KEY": "reasoning-key-456",
}

_CONCURRENCY_VARS = (
    "MAX_SEARCH_CONCURRENCY",
    "MAX_FETCH_CONCURRENCY",
    "MAX_VERIFY_CONCURRENCY",
)

_DEFAULTS = {
    "MAX_SEARCH_CONCURRENCY": 4,
    "MAX_FETCH_CONCURRENCY": 4,
    "MAX_VERIFY_CONCURRENCY": 3,
}

_FIELD_BY_VAR = {
    "MAX_SEARCH_CONCURRENCY": "max_search_concurrency",
    "MAX_FETCH_CONCURRENCY": "max_fetch_concurrency",
    "MAX_VERIFY_CONCURRENCY": "max_verify_concurrency",
}

# Invalid raw string values: either an integer strictly outside [1, 32], or a
# string that does not parse as an integer at all (letters, decimals,
# blank/whitespace, signs-only, etc.).
_out_of_range_ints = st.one_of(
    st.integers(max_value=0),
    st.integers(min_value=33),
).map(str)

_non_integer_text = st.one_of(
    st.text(alphabet=string.ascii_letters, min_size=1, max_size=10),
    st.floats(allow_nan=False, allow_infinity=False).map(lambda f: f"{f:.4f}"),
    st.just(""),
    st.just("   "),
    st.just("+"),
    st.just("-"),
    st.integers(min_value=1, max_value=32).map(lambda v: f"{v}.0"),
)

invalid_concurrency_values = st.one_of(_out_of_range_ints, _non_integer_text)


@given(
    var_name=st.sampled_from(_CONCURRENCY_VARS),
    invalid_value=invalid_concurrency_values,
)
@settings(max_examples=100)
def test_invalid_concurrency_value_always_falls_back_to_default(var_name, invalid_value):
    # Feature: civicpilot, Property 21: Invalid concurrency configuration
    # always falls back to the documented default
    env = dict(VALID_MODEL_ENV, **{var_name: invalid_value})
    result = load_settings(env=env)

    assert getattr(result.concurrency, _FIELD_BY_VAR[var_name]) == _DEFAULTS[var_name]
    assert any(var_name in warning for warning in result.startup_warnings)


@given(
    var_name=st.sampled_from(_CONCURRENCY_VARS),
    valid_value=st.integers(min_value=1, max_value=32),
)
@settings(max_examples=100)
def test_valid_concurrency_value_used_as_is_with_no_warning(var_name, valid_value):
    # Feature: civicpilot, Property 21 (contrasting case): a valid integer in
    # [1, 32] is used as-is and never triggers a startup warning
    env = dict(VALID_MODEL_ENV, **{var_name: str(valid_value)})
    result = load_settings(env=env)

    assert getattr(result.concurrency, _FIELD_BY_VAR[var_name]) == valid_value
    assert result.startup_warnings == []
