"""Process-wide logging: one JSON object per line in staging/production, redacted.

`configure_logging()` installs a dictConfig the API, worker and CLI entrypoints
share. Every record passes through `backend.redaction.redact_text` before it is
emitted, so a credential that reaches a log call cannot reach the log stream.
LOG_FORMAT=text keeps the human format locally; auto resolves by APP_ENV.
"""

from __future__ import annotations

import json
import logging
import logging.config
import traceback
from datetime import UTC, datetime

from backend.redaction import redact_text
from backend.settings import get_settings

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exc"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        return redact_text(json.dumps(payload, default=str))


def configure_logging() -> str:
    """Install handlers per LOG_FORMAT/LOG_LEVEL. Returns the format in effect."""
    settings = get_settings()
    fmt = settings.resolved_log_format
    formatter = (
        "backend.ops_logging.RedactingJsonFormatter"
        if fmt == "json"
        else "backend.ops_logging.RedactingTextFormatter"
    )
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "redact": {"()": formatter, "format": TEXT_FORMAT},
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "formatter": "redact",
                    "stream": "ext://sys.stderr",
                }
            },
            "root": {"level": settings.log_level.upper(), "handlers": ["stdout"]},
            "loggers": {
                # Uvicorn installs its own handlers when it manages logging; we run it
                # with log_config=None so its records propagate here instead.
                "uvicorn": {"handlers": ["stdout"], "propagate": False},
                "uvicorn.error": {"handlers": ["stdout"], "propagate": False},
                "uvicorn.access": {"handlers": ["stdout"], "propagate": False},
            },
        }
    )
    return fmt
