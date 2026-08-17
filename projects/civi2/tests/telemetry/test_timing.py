"""Unit tests for civicpilot.telemetry.timing.

Covers now_s() monotonicity, elapsed_s() correctness, and
millisecond-precision formatting/rounding behavior (Task 1.4).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from civicpilot.telemetry.timing import (
    Stopwatch,
    elapsed_s,
    format_seconds_ms,
    now_s,
    timed,
)


class TestNowS:
    def test_returns_float(self) -> None:
        assert isinstance(now_s(), float)

    def test_is_monotonically_non_decreasing_across_calls(self) -> None:
        readings = [now_s() for _ in range(50)]
        assert readings == sorted(readings)

    def test_matches_time_monotonic_semantics(self) -> None:
        # now_s() should track time.monotonic() directly (small delta window).
        before = time.monotonic()
        value = now_s()
        after = time.monotonic()
        assert before <= value <= after


class TestElapsedS:
    def test_computes_difference_between_two_readings(self) -> None:
        start = 10.0
        end = 12.5
        assert elapsed_s(start, end) == pytest.approx(2.5)

    def test_defaults_end_to_now_when_omitted(self) -> None:
        start = now_s()
        time.sleep(0.05)
        elapsed = elapsed_s(start)
        assert elapsed > 0
        assert elapsed < 1.0  # sanity bound, well above the sleep duration

    def test_zero_duration_when_start_equals_end(self) -> None:
        t = now_s()
        assert elapsed_s(t, t) == 0.0

    def test_raises_on_negative_duration(self) -> None:
        start = 5.0
        end = 4.0
        with pytest.raises(ValueError):
            elapsed_s(start, end)

    def test_real_sleep_produces_plausible_elapsed_time(self) -> None:
        start = now_s()
        time.sleep(0.05)
        elapsed = elapsed_s(start)
        # Generous bounds to avoid flakiness under CI/test-runner scheduling jitter.
        assert 0.03 <= elapsed <= 1.0


class TestFormatSecondsMs:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (1.0, 1.0),
            (0.0, 0.0),
            (1.23456, 1.235),
            (1.2344, 1.234),
            (2.9999, 3.0),
        ],
    )
    def test_rounds_to_millisecond_precision(self, value: float, expected: float) -> None:
        assert format_seconds_ms(value) == pytest.approx(expected)

    def test_result_has_at_most_three_decimal_places(self) -> None:
        formatted = format_seconds_ms(7.123456789)
        # Representable exactly at 3 decimal places once rounded.
        assert formatted == round(formatted, 3)

    def test_raises_on_negative_value(self) -> None:
        with pytest.raises(ValueError):
            format_seconds_ms(-0.001)


class TestStopwatch:
    def test_measures_elapsed_time_around_a_block(self) -> None:
        with Stopwatch() as sw:
            time.sleep(0.05)
        # Generous lower bound to avoid flakiness from OS sleep/timer granularity.
        assert sw.elapsed_s >= 0.03
        assert sw.elapsed_s < 1.0

    def test_elapsed_ms_precision_is_rounded(self) -> None:
        with Stopwatch() as sw:
            time.sleep(0.01)
        assert sw.elapsed_ms_precision == round(sw.elapsed_ms_precision, 3)

    def test_elapsed_s_available_while_running(self) -> None:
        with Stopwatch() as sw:
            time.sleep(0.01)
            mid_elapsed = sw.elapsed_s
            assert mid_elapsed >= 0.0


class TestTimedDecorator:
    def test_sync_function_records_last_elapsed_s(self) -> None:
        @timed
        def slow() -> str:
            time.sleep(0.01)
            return "done"

        result = slow()
        assert result == "done"
        assert slow.last_elapsed_s >= 0.0

    @pytest.mark.asyncio
    async def test_async_function_records_last_elapsed_s(self) -> None:
        @timed
        async def slow_async() -> str:
            await asyncio.sleep(0.01)
            return "done"

        result = await slow_async()
        assert result == "done"
        assert slow_async.last_elapsed_s >= 0.0

    def test_preserves_function_return_value_on_exception(self) -> None:
        @timed
        def failing() -> None:
            time.sleep(0.01)
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            failing()
        # Timing is still recorded even when the wrapped function raises.
        assert failing.last_elapsed_s >= 0.0
