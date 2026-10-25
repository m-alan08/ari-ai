"""Secret redaction applied before anything is persisted, logged or sent to a model."""

from __future__ import annotations

import re
from typing import Any

# Token- and transport-shaped patterns safe to apply to any text, including
# source code: every match is self-contained and never rewrites syntax outside
# the secret itself. Code-aware redaction (``redact_code``) applies ONLY this
# list plus the quoted-literal rule below — never the free-text key=value
# matcher, which would corrupt expressions like ``api_key=os.environ["K"]``.
_HIGH_CONFIDENCE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "[REDACTED:anthropic_key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED:github_token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED:github_token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:aws_key]"),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"), "[REDACTED:slack_token]"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED:private_key]",
    ),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}"), r"\1 [REDACTED:bearer]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{2,}"), "[REDACTED:jwt]"),
    # credentials inside connection strings: scheme://user:pass@host -> scheme://[REDACTED]@host
    (re.compile(r"\bsk_live_[A-Za-z0-9]{8,}\b"), "[REDACTED:stripe_key]"),
    # Credentials embedded in driver URLs in connector errors
    # (``postgresql://svc:secret@db.internal/app``). Only rewrites the
    # ``scheme://user:pass@`` span, so it is safe inside quoted code literals.
    # Runs before the DSN-specific pattern below so that marker wins the name.
    (
        re.compile(r"(?i)([a-z][a-z0-9+.\-]*://[^/\s:@]+):[^@\s/]+@"),
        r"\1:[REDACTED:uri_password]@",
    ),
    (
        re.compile(
            r"(?i)\b(postgres(?:ql)?(?:\+\w+)?|mysql(?:\+\w+)?|mongodb(?:\+srv)?|redis|amqp)://[^\s:/@]+:[^\s@]+@"
        ),
        r"\1://[REDACTED:dsn_credentials]@",
    ),
]
_KV_KEY = r"\w*(?:password|passwd|pwd|api_key|apikey|secret|token)\w*"
_KV_PATTERN = re.compile(r"(?i)\b" + _KV_KEY + r"\s*[=:]\s*(?:'[^'\n]*'|\"[^\"\n]*\"|[^\s'\"]+)")
# Code-aware key=value matcher. Three prefix shapes, all ending in a quoted
# literal — only the literal is rewritten, so unquoted non-secret expressions
# (``api_key=os.environ["API_KEY"]``, ``password: str = os.getenv("P")``) are
# left byte-identical.
_CODE_KV_PATTERN = re.compile(
    r"(?i)(?P<prefix>(?:"
    + r"\b"
    + _KV_KEY
    + r"\s*:\s*[^=\n;#]+?=\s*"  # annotated: name: Type = "..." (annot span can't cross ; or #)
    + r"|\b"
    + _KV_KEY
    + r"\s*[=:]\s*"  # simple: name = "..." or name: "..."
    + r"|['\"]"
    + _KV_KEY
    + r"['\"](?:\s*\])?\s*[:=]\s*"  # quoted mapping key/subscript: {"name": "..."}
    + r"))"
    + r"(?P<literal>'(?:\\.|[^'\\\n])+'|\"(?:\\.|[^\"\\\n])+\")"
)
# Free-text-only matcher: catches credential-looking ``key=value`` fragments
# ``_KV_PATTERN`` misses (hyphenated spellings like ``api-key``/``access-token``).
# Deliberately excluded from ``_HIGH_CONFIDENCE_PATTERNS``/``redact_code``: in
# source, ``api_key=os.environ["API_KEY"]`` is a non-secret expression and must
# not be rewritten.
_GENERIC_KV_PATTERN = (
    re.compile(
        r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|"
        r"client[_-]?secret)=([^\s&'\"]+)"
    ),
    r"\1=[REDACTED]",
)
_PATTERNS = [*_HIGH_CONFIDENCE_PATTERNS, (_KV_PATTERN, "[REDACTED:kv]"), _GENERIC_KV_PATTERN]

# Deliberately NOT scrubbed: absolute paths and internal hostnames. They aid
# debugging and are bounded to project members; revisit if members must be
# isolated from infra internals (see docs/release/RELEASE_GATE_2026-10-24.md).

_SENSITIVE_KEYS = re.compile(
    r"(?i)^(password|passwd|secret|client_secret|token|access_token|refresh_token|api_key|"
    r"private_key|authorization|cookie|set-cookie|x-hub-signature(-256)?)$"
)


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_code(text: str) -> str:
    """Redact source and diffs without rewriting unquoted code expressions."""
    for pattern, replacement in _HIGH_CONFIDENCE_PATTERNS:
        text = pattern.sub(replacement, text)

    def redact_quoted_value(match: re.Match[str]) -> str:
        literal = match.group("literal")
        quote = literal[0]
        return f"{match.group('prefix')}{quote}[REDACTED:kv]{quote}"

    return "".join(
        _CODE_KV_PATTERN.sub(redact_quoted_value, line) for line in text.splitlines(keepends=True)
    )


def find_high_confidence_secret(text: str) -> str | None:
    # Labeling checks the most specific transport-DSN pattern before the generic
    # URI-password pattern; substitution order in _PATTERNS is the reverse.
    ordered = sorted(_HIGH_CONFIDENCE_PATTERNS, key=lambda pair: "uri_password" in pair[1])
    for pattern, replacement in ordered:
        match = re.search(r"\[REDACTED:([a-z0-9_]+)\]", replacement)
        if match is None:
            continue
        label = match.group(1)
        if label in {"bearer", "jwt"}:
            continue
        if pattern.search(text):
            return label
    return None


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
