"""Worker-side helpers for hooks-lite: signature verification and artifact fetch."""

from __future__ import annotations

import hashlib
import hmac
import ssl
import time
import urllib.request


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """HMAC-SHA256 over the raw body; required on every webhook delivery."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature_header.removeprefix("sha256="))


def delivery_is_fresh(delivery_epoch: float, *, now: float | None = None, tolerance_s: float = 300.0) -> bool:
    """Reject replays: deliveries must carry a recent timestamp."""
    return abs((now or time.time()) - delivery_epoch) <= tolerance_s


def fetch_artifact(url: str, *, timeout: float = 10.0) -> bytes:
    """Download a build artifact over verified TLS."""
    context = ssl.create_default_context()
    with urllib.request.urlopen(url, timeout=timeout, context=context) as resp:
        return resp.read(1 << 20)
