"""Secret redaction applied before anything is persisted, logged or sent to a model."""

from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "[REDACTED:anthropic_key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED:github_token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED:github_token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:aws_key]"),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"), "[REDACTED:slack_token]"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED:private_key]",
    ),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{16,}"), r"\1 [REDACTED:bearer]"),
    # credentials inside DSN userinfo: scheme://user:pass@host -> scheme://[REDACTED]@host
    (
        re.compile(
            r"(?i)\b(postgres(?:ql)?(?:\+\w+)?|mysql(?:\+\w+)?|mongodb(?:\+srv)?|redis|amqp)://[^\s:/@]+:[^\s@]+@"
        ),
        r"\1://[REDACTED:dsn_credentials]@",
    ),
    # key=value / key: value secrets in prose (password=..., api_key: ...)
    (
        re.compile(
            r"(?i)\b\w*(?:password|passwd|pwd|api_key|apikey|secret|token)\w*\s*[=:]\s*"
            r"(?:'[^'\n]*'|\"[^\"\n]*\"|[^\s'\"]+)"
        ),
        "[REDACTED:kv]",
    ),
]

_SENSITIVE_KEYS = re.compile(
    r"(?i)^(password|passwd|secret|client_secret|token|access_token|refresh_token|api_key|"
    r"private_key|authorization|cookie|set-cookie|x-hub-signature(-256)?)$"
)


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact(value: Any) -> Any:
    """Recursively redact strings and values under credential-like keys."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            k: (
                "[REDACTED]" if isinstance(k, str) and _SENSITIVE_KEYS.match(k) and v else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value
