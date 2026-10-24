"""Structured logging: JSON lines, redacted secrets, text fallback in dev."""

from __future__ import annotations

import json
import logging

from backend.ops_logging import RedactingJsonFormatter, RedactingTextFormatter


def _record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)


def test_json_formatter_emits_parseable_line() -> None:
    out = RedactingJsonFormatter().format(_record("job %s succeeded", "job_9"))
    payload = json.loads(out)
    assert payload["level"] == "info" and payload["msg"] == "job job_9 succeeded"


def test_json_formatter_redacts_secrets() -> None:
    out = RedactingJsonFormatter().format(_record("key sk-ant-abcdefghijklmnop leaked"))
    assert "sk-ant" not in out
    assert "[REDACTED:anthropic_key]" in out


def test_text_formatter_also_redacts() -> None:
    out = RedactingTextFormatter("%(message)s").format(_record("bearer abcdefghijklmnop1234"))
    assert "abcdefghijklmnop1234" not in out


def test_resolved_log_format(settings_override) -> None:
    from backend.settings import get_settings

    settings_override(LOG_FORMAT="json")
    assert get_settings().resolved_log_format == "json"
    settings_override(LOG_FORMAT="auto", APP_ENV="production")
    assert get_settings().resolved_log_format == "json"
    settings_override(LOG_FORMAT="auto", APP_ENV="development")
    assert get_settings().resolved_log_format == "text"
