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
    + r"(?P<literal>(?P<string_prefix>[rRbBfFuU]{0,2})(?P<body>"
    + r"'''(?:(?!''').)+'''|\"\"\"(?:(?!\"\"\").)+\"\"\""  # single-line triple-quoted
    + r"|'(?:\\.|[^'\\\n])+'|\"(?:\\.|[^\"\\\n])+\"))"
)
# Unquoted ``key: value`` (YAML) and ``KEY=value`` (dotenv) values. Only applied to lines
# of a diff whose file header says the file is YAML or dotenv: in source code the same
# shapes are expressions (``password: str = ...``, ``api_key=os.environ[...]``).
# Values that reference another setting (``${VAR}``), YAML tags/anchors/aliases, block
# scalar markers and flow collections are not literal secrets and are left alone.
_UNQUOTED_VALUE = r"(?P<value>(?![$&*!|>{\['\"#])\S+(?:[ \t]+(?!#)\S+)*)"
_YAML_KV_PATTERN = re.compile(
    r"(?i)^(?P<prefix>\s*(?:-\s+)?" + _KV_KEY + r"\s*:\s+)" + _UNQUOTED_VALUE
)
_DOTENV_KV_PATTERN = re.compile(
    r"(?i)^(?P<prefix>\s*(?:export\s+)?" + _KV_KEY + r"\s*=\s*)" + _UNQUOTED_VALUE
)
# ``- KEY=value`` list entries (docker-compose ``environment:``) inside YAML.
_YAML_LIST_ENV_PATTERN = re.compile(
    r"(?i)^(?P<prefix>\s*-\s+" + _KV_KEY + r"\s*=\s*)" + _UNQUOTED_VALUE
)
# INI/cfg/conf/properties: dotted or dashed keys (``spring.datasource.password``), ``=`` or ``:``.
_PROPERTIES_KV_PATTERN = re.compile(
    r"(?i)^(?P<prefix>\s*(?:[\w\-]+[.\-])*" + _KV_KEY + r"\s*[=:]\s*)" + _UNQUOTED_VALUE
)
_DOCKERFILE_KV_PATTERN = re.compile(
    r"(?i)^(?P<prefix>\s*(?:ENV|ARG)\s+" + _KV_KEY + r"(?:\s*=\s*|\s+))" + _UNQUOTED_VALUE
)
_XML_KV_PATTERN = re.compile(
    r"(?i)(?P<prefix><(?P<tag>" + _KV_KEY + r")>)(?P<value>(?![$\s])[^<\n]+)(?=</(?P=tag)>)"
)
_UNQUOTED_KV_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "yaml": (_YAML_KV_PATTERN, _YAML_LIST_ENV_PATTERN),
    "dotenv": (_DOTENV_KV_PATTERN,),
    "properties": (_PROPERTIES_KV_PATTERN,),
    "dockerfile": (_DOCKERFILE_KV_PATTERN,),
    "xml": (_XML_KV_PATTERN,),
}
_DIFF_FILE_HEADER = re.compile(r"^(?:\+\+\+ (?:b/)?(?P<plus>\S+)|diff --git a/\S+ b/(?P<git>\S+))")
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


def _file_format(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1].lower()
    if name.endswith((".yml", ".yaml")):
        return "yaml"
    if name in (".env", "env") or name.startswith(".env.") or name.endswith(".env"):
        return "dotenv"
    if name.endswith((".sh", ".bash", ".zsh")):
        return "dotenv"
    if name.endswith((".ini", ".cfg", ".conf", ".properties")):
        return "properties"
    if name == "dockerfile" or name.startswith("dockerfile.") or name.endswith(".dockerfile"):
        return "dockerfile"
    if name.endswith(".xml"):
        return "xml"
    return None


def redact_code(text: str) -> str:
    """Redact source and diffs without rewriting unquoted code expressions.

    Quoted literals (including r/b/f-prefixed and single-line triple-quoted ones) under a
    credential-like name are redacted everywhere. Unquoted values (YAML, dotenv/shell,
    INI/properties, Dockerfile ENV/ARG, XML elements) are redacted only inside a diff section
    whose ``+++``/``diff --git`` header names such a file."""
    for pattern, replacement in _HIGH_CONFIDENCE_PATTERNS:
        text = pattern.sub(replacement, text)

    def redact_quoted_value(match: re.Match[str]) -> str:
        body = match.group("body")
        quote = body[:3] if body[:3] in ("'''", '"""') else body[0]
        return f"{match.group('prefix')}{match.group('string_prefix')}{quote}[REDACTED:kv]{quote}"

    def redact_unquoted_value(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}[REDACTED:kv]"

    out: list[str] = []
    file_format: str | None = None
    for line in text.splitlines(keepends=True):
        header = _DIFF_FILE_HEADER.match(line)
        if header is not None:
            file_format = _file_format(header.group("plus") or header.group("git"))
            out.append(line)
            continue
        line = _CODE_KV_PATTERN.sub(redact_quoted_value, line)
        if file_format is not None and line[:1] in ("+", "-", " "):
            marker, content = line[:1], line[1:]
            for kv in _UNQUOTED_KV_PATTERNS[file_format]:
                content = kv.sub(redact_unquoted_value, content, count=1)
            line = marker + content
        out.append(line)
    return "".join(out)


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
