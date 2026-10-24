"""Baseline tests for hooks-lite."""

import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from receiver import WEBHOOK_SECRET, create_app

OPS = {"Authorization": "Bearer fixture-token-ops"}


def _sig(body: bytes) -> str:
    return "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_health():
    assert TestClient(create_app()).get("/health").status_code == 200


def test_webhook_rejects_unsigned():
    resp = TestClient(create_app()).post("/webhooks/github", content=b"{}")
    assert resp.status_code == 401


def test_webhook_accepts_signed_fresh():
    body = b'{"ok": true}'
    resp = TestClient(create_app()).post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Signature-256": _sig(body),
            "X-Delivery-Id": "d1",
            "X-Delivery-Epoch": str(time.time()),
        },
    )
    assert resp.status_code == 202


def test_job_requires_session():
    assert TestClient(create_app()).get("/jobs/job_a1").status_code == 401


def test_job_status():
    resp = TestClient(create_app()).get("/jobs/job_a1", headers=OPS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


def test_login_sets_cookie():
    resp = TestClient(create_app()).post(
        "/sessions/login", json={"principal": "svc_ops", "password": "fixture-password"}
    )
    assert resp.status_code == 200
    assert "hooks_session=" in resp.headers.get("set-cookie", "")
