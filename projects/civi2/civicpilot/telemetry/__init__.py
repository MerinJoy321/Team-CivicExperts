"""Telemetry module: per-stage timings, concurrency metrics, and
Critical_Path_Latency computation (Requirement 4).
"""

from civicpilot.telemetry.telemetry_module import BatchRecord, TelemetryModule
from civicpilot.telemetry.timing import (
    Stopwatch,
    elapsed_s,
    format_seconds_ms,
    now_s,
    timed,
)

__all__ = [
    "BatchRecord",
    "Stopwatch",
    "TelemetryModule",
    "elapsed_s",
    "format_seconds_ms",
    "now_s",
    "timed",
]
