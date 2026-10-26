"""Deterministic text/code featurisation shared by training and inference.

Feature hashing (the "hashing trick") of word unigrams/bigrams and character trigrams into a
fixed number of buckets, with signed hashing to reduce collision bias and log-scaled counts,
L2-normalised per field. Pure Python and stable across processes (crc32, not ``hash()``), so a
checkpoint trained offline scores identically inside the worker.
"""

from __future__ import annotations

import math
import re
import zlib
from collections.abc import Iterable

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|==|!=|<=|>=|\*\*|->|[^\sA-Za-z0-9_]")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokens(text: str) -> list[str]:
    """Code-aware tokens: identifiers split on snake/camel case, plus operators and punctuation."""
    out: list[str] = []
    for tok in _WORD.findall(text):
        if tok[0].isalpha() or tok[0] == "_":
            low = tok.lower()
            out.append(low)
            parts = [p for chunk in tok.split("_") for p in _CAMEL.split(chunk) if p]
            if len(parts) > 1:
                out.extend(p.lower() for p in parts)
        else:
            out.append(tok)
    return out


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    h = zlib.crc32(feature.encode("utf-8"))
    return h % dim, (1.0 if (h >> 31) & 1 else -1.0)


def featurize(
    fields: Iterable[tuple[str, str]], dim: int, *, char_ngrams: bool = True
) -> dict[int, float]:
    """Sparse feature vector {bucket: value} for named text fields.

    Each field is namespaced (``code:``, ``cwe:``…) so the same word in two fields stays distinct.
    """
    fields = list(fields)
    out: dict[int, float] = {}
    present = [(n, t) for n, t in fields if t]
    for name, text in present:
        counts: dict[int, float] = {}
        toks = tokens(text)
        feats = [f"{name}:{t}" for t in toks]
        feats += [f"{name}:{a}|{b}" for a, b in zip(toks, toks[1:], strict=False)]
        if char_ngrams:
            squashed = re.sub(r"\s+", " ", text.lower())
            feats += [f"{name}#{squashed[i : i + 3]}" for i in range(max(0, len(squashed) - 2))]
        for f in feats:
            idx, sign = _bucket(f, dim)
            counts[idx] = counts.get(idx, 0.0) + sign
        vec = {i: math.copysign(math.log1p(abs(v)), v) for i, v in counts.items() if v}
        # Each field is L2-normalised on its own, so a one-token field (a CWE id) weighs as much
        # as a long code span instead of being drowned out by it.
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        scale = 1.0 / (norm * math.sqrt(len(present)))
        for i, v in vec.items():
            out[i] = out.get(i, 0.0) + v * scale
    return out
