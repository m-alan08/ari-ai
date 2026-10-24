"""GET /api/v1/ops/status: token gating plus queue and worker visibility."""

from __future__ import annotations

import sqlalchemy as sa

from backend import jobs as jobq
from backend.db import get_engine


def test_ops_status_open_in_dev(client_factory) -> None:
    c = client_factory()
    r = c.get("/api/v1/ops/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["migrations"]["at_head"] is True
    assert "counts" in body["queue"]


def test_ops_status_requires_token_when_configured(client_factory, settings_override) -> None:
    settings_override(OPS_TOKEN="ops-secret")
    c = client_factory()
    assert c.get("/api/v1/ops/status").status_code == 403
    assert c.get("/api/v1/ops/status", headers={"X-Ops-Token": "wrong"}).status_code == 403
    r = c.get("/api/v1/ops/status", headers={"X-Ops-Token": "ops-secret"})
    assert r.status_code == 200


def test_ops_status_reports_queue_and_workers(client_factory, settings_override) -> None:
    settings_override(OPS_TOKEN="ops-secret")
    with get_engine().begin() as conn:
        jobq.enqueue(conn, "wibble", {"x": 1})
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, kind, payload, status, run_after, max_attempts,"
                " lease_owner, lease_expires_at, heartbeat_at)"
                " VALUES ('job_running1', 'wobble', '{}', 'running', now(), 5,"
                " 'host:123', now() + interval '60 seconds', now())"
            )
        )
    c = client_factory()
    r = c.get("/api/v1/ops/status", headers={"X-Ops-Token": "ops-secret"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queue"]["counts"].get("queued") == 1
    assert body["queue"]["counts"].get("running") == 1
    assert body["queue"]["running"][0]["owner"] == "host:123"
    assert body["queue"]["workers_with_leases"][0]["owner"] == "host:123"
    assert body["first_responder"]["default_mode"] == "off"
