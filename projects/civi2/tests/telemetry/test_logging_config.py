"""Tests for civicpilot.telemetry.logging_config (Task 1.3).

These are basic tests confirming the structured logger emits JSON-lines
output with the required fields, respects the LOG_LEVEL environment
variable, and applies the basic credential-shaped-string caution at INFO+.
"""

from __future__ import annotations

import json
import logging

import pytest

from civicpilot.telemetry import logging_config
from civicpilot.telemetry.logging_config import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _reset_logging_state(monkeypatch):
    """Ensure each test starts from a clean, unconfigured logging state so
    LOG_LEVEL changes and handler attachment don't leak across tests.
    """
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    logging_config._CONFIGURED = False
    root = logging.getLogger("civicpilot")
    root.handlers.clear()
    yield
    logging_config._CONFIGURED = False
    root.handlers.clear()


def test_get_logger_emits_structured_json_with_required_fields(capsys):
    logger = get_logger("civicpilot.scheduler.core")
    logger.info("dispatching task", extra={"task_id": "t-1", "category": "SEARCH"})

    captured = capsys.readouterr()
    line = captured.err.strip().splitlines()[-1]
    record = json.loads(line)

    assert record["logger"] == "civicpilot.scheduler.core"
    assert record["level"] == "INFO"
    assert record["message"] == "dispatching task"
    assert record["task_id"] == "t-1"
    assert record["category"] == "SEARCH"
    assert "timestamp" in record


def test_get_logger_namespaces_bare_module_names(capsys):
    logger = get_logger("scheduler.core")
    assert logger.name == "civicpilot.scheduler.core"

    logger.info("hello")
    captured = capsys.readouterr()
    record = json.loads(captured.err.strip().splitlines()[-1])
    assert record["logger"] == "civicpilot.scheduler.core"


def test_default_log_level_is_info_and_debug_is_suppressed(capsys):
    logger = get_logger("civicpilot.tools.search")
    logger.debug("this should not appear")
    logger.info("this should appear")

    captured = capsys.readouterr()
    lines = [line for line in captured.err.strip().splitlines() if line]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["message"] == "this should appear"


def test_log_level_env_var_is_respected(monkeypatch, capsys):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    configure_logging(force=True)

    logger = get_logger("civicpilot.pipeline.intake")
    logger.debug("debug detail")

    captured = capsys.readouterr()
    lines = [line for line in captured.err.strip().splitlines() if line]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["level"] == "DEBUG"
    assert record["message"] == "debug detail"


def test_credential_shaped_strings_are_masked_at_info_level(capsys):
    logger = get_logger("civicpilot.tools.model_client")
    logger.info("calling provider with api_key=sk-super-secret-value-123")

    captured = capsys.readouterr()
    record = json.loads(captured.err.strip().splitlines()[-1])
    assert "sk-super-secret-value-123" not in record["message"]
    assert "REDACTED" in record["message"]
