"""Alert delivery: configured webhook posts redacted JSON; failures never raise."""

from __future__ import annotations

import json

import httpx

from backend import alerts


def test_no_webhook_configured_returns_false() -> None:
    assert alerts.send_alert("job_dead") is False


def test_alert_posts_redacted_payload(settings_override) -> None:
    settings_override(ALERT_WEBHOOK_URL="https://hooks.example/xyz")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.read())
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ok = alerts.send_alert(
        "job_dead",
        details={"job_id": "job_1", "error": "token ghp_abcdefghijklmnopqrstuvwxyz leaked"},
        client=client,
    )
    assert ok is True
    body = seen["body"]
    assert body["event"] == "job_dead"
    assert "ghp_" not in json.dumps(body)
    assert "[REDACTED:github_token]" in json.dumps(body)


def test_alert_webhook_error_is_swallowed(settings_override) -> None:
    settings_override(ALERT_WEBHOOK_URL="https://hooks.example/xyz")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert alerts.send_alert("job_dead", client=client) is False
