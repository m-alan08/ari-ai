"""Reject ingest payloads that look like they contain secrets. Pure over JSON.

Quarantine records the *reason*, never the secret values. The ingest path must
not persist extra secrets.
"""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEY = re.compile(
    r"(password|secret|authorization|cookie|api[_-]?key|private[_-]?key|ssn|\bemail\b)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_JWTISH = re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}")
_SECRET_VALUE = re.compile(
    r"(password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key)\s*[=:]\s*\S+",
    re.IGNORECASE,
)


def secret_like_reason(payload: Any) -> str | None:
    """Return a quarantine reason if ``payload`` looks like it contains secrets."""
    hits = _walk(payload, "")
    if not hits:
        return None
    kinds = sorted({h.split(":")[0] for h in hits})
    return "payload contained disallowed secret-like fields (" + ", ".join(kinds) + "); dropped"


def _walk(value: Any, path: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and _SECRET_KEY.search(key):
                found.append(f"key:{key}")
            found.extend(_walk(item, here))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk(item, path))
    elif isinstance(value, str):
        if _EMAIL.search(value):
            found.append("email")
        if _BEARER.search(value) or _JWTISH.search(value):
            found.append("token")
        if _SECRET_VALUE.search(value):
            found.append("credential")
    return found
