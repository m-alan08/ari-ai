"""Critical-failure alerting: one POST to ALERT_WEBHOOK_URL per event.

Deliberately dependency-free and loss-tolerant: the payload is Slack-compatible
(a top-level ``text`` plus structured fields) so any incoming-webhook URL works,
and every failure is logged and swallowed — alerting must never take a worker
down with it. Details pass through `redact` before leaving the process.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from backend.redaction import redact
from backend.settings import get_settings

log = logging.getLogger("backend.alerts")

_TIMEOUT_S = 5.0


def send_alert(
    event: str,
    *,
    severity: str = "critical",
    details: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> bool:
    """POST an alert to the configured webhook. Returns False when not configured."""
    url = get_settings().alert_webhook_url
    if url is None or not url.get_secret_value():
        return False
    settings = get_settings()
    safe_details = redact(details or {})
    body = {
        "text": f"[{severity.upper()}] ari-ai {settings.app_env}: {event}",
        "event": event,
        "severity": severity,
        "env": settings.app_env,
        "host": os.uname().nodename if hasattr(os, "uname") else "unknown",
        "details": safe_details,
    }
    owns = client is None
    client = client or httpx.Client(timeout=_TIMEOUT_S)
    try:
        resp = client.post(url.get_secret_value(), json=body)
        if resp.status_code >= 300:
            log.warning("alert webhook returned %s for %s", resp.status_code, event)
            return False
        return True
    except Exception:
        log.warning("alert webhook delivery failed for %s", event, exc_info=True)
        return False
    finally:
        if owns:
            client.close()
