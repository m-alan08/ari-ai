"""Conservative line-preserving redaction before storing or returning source context."""

import re

from shared.evidence import EvidenceRef

_PEM = re.compile(r"-----BEGIN [^-\n]*PRIVATE KEY-----[\s\S]*?-----END [^-\n]*PRIVATE KEY-----")
_KNOWN = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{20,}|sk_live_[A-Za-z0-9]{8,}"
    r"|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}"
)
# JWTs: three base64url segments, the first always starting `eyJ`.
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}")
# env-var defaults: os.environ.get("X_PASSWORD", "default")
_ENV_DEFAULT = re.compile(
    r"(environ(?:\.get)?[ \t]*\([ \t]*[\"'][^\"']*"
    r"(?i:secret|password|passwd|token|key)[^\"']*[\"'][ \t]*,[ \t]*)"
    r"([rbuf]{0,2})(\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*')"
)
_LIT = (
    r"(?:\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''"
    r"|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*')"
)
# Replace only the assigned value: string literal(s) — possibly parenthesized
# implicit concat `("a" "b")` or comma-continued `"a", "b"` — or a bare token that
# ends at a line/clause boundary — EOL/comment (any token), or a mid-line `,`/`;`/
# closing bracket. Never the rest of the line and never a token followed by `(` or
# an operator, so `secret_configured=bool(` stays parseable.
_ASSIGN = re.compile(
    r"(?im)([\w.-]*(?:secret|password|passwd|api_key|token|private_key)"
    r"[\w.-]*[\"']?[ \t]*[:=][ \t]*)"
    r"(?:([rbuf]{0,2})(" + _LIT + r"(?:[ \t\n]*,[ \t]*" + _LIT + r")*)(?![\w\"'])"
    r"|\([ \t\n]*" + _LIT + r"(?:[ \t\n]+" + _LIT + r")*[ \t\n]*\)"
    r"|[^\s\"'#,;()\[\]{}=](?:[^\n\"'#,;()\[\]{}=]*[^\s\"'#,;()\[\]{}=])?"
    r"(?=[ \t]*[,;]?[ \t]*(?:#[^\n]*)?$)"
    r"|[^\s\"'#,;()\[\]{}=][^\n\"'#,;()\[\]{}=]*(?=[ \t]*[,;)\]}]))"
)


def _redact_literals(text: str) -> str:
    def _one(m: re.Match[str]) -> str:
        lit = m[0]
        quote = lit[:3] if lit[:3] in ('"""', "'''") else lit[0]
        return quote + "[REDACTED]" + "\n" * lit.count("\n") + quote

    return re.sub(_LIT, _one, text)


def _assigned(match: re.Match[str]) -> str:
    rest = match[0][len(match[1]) :]
    if match[3] is None:
        if rest.startswith("("):
            return match[1] + _redact_literals(rest)
        # `key: token` mid-line keeps digit-less identifiers (annotations like
        # `secret: bytes`); `key=token` mid-line is always redacted.
        if match[1].rstrip().endswith(":") and not any(c.isdigit() for c in rest):
            return match[0]
        return match[1] + "[REDACTED]"
    return match[1] + match[2] + _redact_literals(match[3])


_BEARER = re.compile(r"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/=-]+")
_URL_CREDENTIAL = re.compile(
    r"(?i)\b((?:https?|postgres(?:ql)?(?:\+\w+)?|mysql(?:\+\w+)?|mongodb(?:\+srv)?|redis|amqp)://)"
    r"[^/\s:@]+:[^/\s@]+@"
)


def redact(text: str) -> str:
    text = _PEM.sub(lambda m: "[REDACTED]" + "\n" * m[0].count("\n"), text)
    text = _KNOWN.sub("[REDACTED]", text)
    text = _JWT.sub("[REDACTED]", text)
    text = _ENV_DEFAULT.sub(lambda m: m[1] + m[2] + _redact_literals(m[3]), text)
    text = _ASSIGN.sub(_assigned, text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    return _URL_CREDENTIAL.sub(r"\1[REDACTED]@", text)


def redact_evidence(evidence: EvidenceRef) -> EvidenceRef:
    """Copy free-form citation metadata for context output, preserving stored evidence.

    Identity fields (SHA, artifact, path, span and symbol) remain exact. Redaction can
    expand a short token, so bound the display metadata to canonical ShortText's
    500-character limit before validating the new reference.
    """
    return EvidenceRef.model_validate(
        {
            **evidence.model_dump(),
            "provenance": redact(evidence.provenance)[:500],
            "note": redact(evidence.note)[:500] if evidence.note is not None else None,
        }
    )
