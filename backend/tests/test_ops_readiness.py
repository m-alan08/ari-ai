"""Readiness/diagnostics contract: capability split, worker liveness, deploy identity."""

from __future__ import annotations

import sqlalchemy as sa
from helpers import WEBHOOK_SECRET  # noqa: F401  (kept for parity with core tests)


def _readyz(c):
    r = c.get("/readyz")
    return r.status_code, r.json()


def test_readyz_reports_capabilities_separately(client_factory) -> None:
    code, body = _readyz(client_factory())
    assert code == 200
    assert body["status"] == "ready"
    assert body["database"] and body["queue"]
    # capabilities block names each assessment dependency separately
    caps = body["capabilities"]
    assert caps["llm"]["mode"] == "unavailable"  # no key in tests: never pretends live
    assert caps["llm"]["configured"] is False
    assert caps["github"]["app_configured"] is False
    assert caps["github"]["webhook_secret_configured"] is True
    assert caps["sandbox"]["mode"] in ("docker", "local-dev", "vercel")
    assert caps["artifact_store"]["writable"] is True
    assert body["capabilities_ready"] is False  # llm+github unset → visibly not capable
    assert body["workers"]["required"] is False  # APP_ENV=test: workers informational
    assert body["schema_version"]
    assert body["git_sha"] is None  # no deploy sha in tests


def test_readyz_git_sha_and_schema_version(client_factory, settings_override) -> None:
    settings_override(RAILWAY_GIT_COMMIT_SHA="abc123" * 6 + "abcd")
    _, body = _readyz(client_factory())
    assert body["git_sha"] == "abc123" * 6 + "abcd"
    from shared import SCHEMA_VERSION

    assert body["schema_version"] == SCHEMA_VERSION


def test_readyz_degraded_status_when_capability_missing(client_factory, settings_override) -> None:
    # Infra healthy (db+queue+no worker requirement) but LLM/GitHub unconfigured:
    # service serves traffic, pilot capability is false — visible, not a bare 200.
    settings_override(APP_ENV="test", READYZ_REQUIRE_WORKERS="false")
    code, body = _readyz(client_factory())
    assert code == 200 and body["status"] == "ready"
    assert body["capabilities_ready"] is False


def test_worker_heartbeat_makes_worker_visible(client_factory) -> None:
    from backend import jobs
    from backend.db import transaction

    with transaction() as conn:
        jobs.worker_heartbeat(conn, "host:1234", poll=True)
    _, body = _readyz(client_factory())
    assert body["workers"]["alive"] == 1
    assert body["workers"]["freshest_heartbeat_age_s"] is not None

    # A stale heartbeat is not a live worker.
    with transaction() as conn:
        conn.execute(
            sa.text("UPDATE worker_heartbeats SET heartbeat_at = now() - interval '2 hours'")
        )
    _, body = _readyz(client_factory())
    assert body["workers"]["alive"] == 0


def test_stopped_claim_loop_is_not_a_live_worker(client_factory) -> None:
    """Heartbeat alone cannot claim a healthy worker: a process whose main claim loop
    stopped (last_poll_at stale, no job held) does not count even though the row's
    heartbeat_at is fresh — e.g. a lingering lease-thread beat or a stale write."""
    from backend import jobs
    from backend.db import transaction

    with transaction() as conn:
        jobs.worker_heartbeat(conn, "host:idle-hung", poll=True)
        conn.execute(
            sa.text(
                "UPDATE worker_heartbeats SET last_poll_at = now() - interval '2 hours', "
                "current_job_id = NULL"
            )
        )
        # lease-thread style beat: heartbeat_at fresh, poll untouched
        jobs.worker_heartbeat(conn, "host:idle-hung", job_id=None)
    _, body = _readyz(client_factory())
    assert body["workers"]["alive"] == 0

    # A worker genuinely mid-job: main loop has not polled (it is inside the handler)
    # but it holds a claimed job — alive via the job branch, and visibly busy.
    with transaction() as conn:
        jobs.worker_heartbeat(conn, "host:busy", job_id="job_1")
        conn.execute(
            sa.text(
                "UPDATE worker_heartbeats SET last_poll_at = now() - interval '1 hour' "
                "WHERE worker_id = 'host:busy'"
            )
        )
    _, body = _readyz(client_factory())
    assert body["workers"]["alive"] == 1
    assert body["workers"]["busy"] == 1


def test_readyz_requires_workers_in_staging(client_factory, settings_override) -> None:
    settings_override(APP_ENV="staging")
    code, body = _readyz(client_factory())
    # Infra is up (db+queue) but no worker heartbeats → degraded, 503 so deploys don't
    # report healthy while assessments could never drain.
    assert code == 503 and body["status"] == "degraded"
    assert body["workers"]["required"] is True and body["workers"]["alive"] == 0

    from backend import jobs
    from backend.db import transaction

    with transaction() as conn:
        jobs.worker_heartbeat(conn, "host:w1", poll=True)
    code, body = _readyz(client_factory())
    assert code == 200 and body["status"] == "ready"
    assert body["workers"]["alive"] == 1
    # still visibly not assessment-capable: no LLM key / GitHub App in tests
    assert body["capabilities_ready"] is False


def test_diagnostics_requires_authenticated_owner(client_factory) -> None:
    """Public /readyz stays load-balancer friendly; /diagnostics leaks worker ids,
    env-var names and migration state, so it is owner-only."""
    r = client_factory().get("/diagnostics")  # no session at all
    assert r.status_code == 401
    r = client_factory("ops-owner").get("/diagnostics")  # signed-in org owner
    assert r.status_code == 200


def test_diagnostics_reports_migrations_and_workers(client_factory) -> None:
    from backend import jobs
    from backend.db import transaction

    with transaction() as conn:
        jobs.worker_heartbeat(conn, "host:w-diag", job_id="job_x")
    r = client_factory("ops-owner").get("/diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["migrations"]["pending"] == []
    assert body["migrations"]["expected_head"] == "0017"
    assert body["jobs"]["queued"] == 0 and set(body["jobs"]) >= {"queued", "running"}
    assert any(w["worker_id"] == "host:w-diag" for w in body["worker_rows"])
    assert body["capabilities"]["github"]["verified"]["verified"] is False  # no app creds


def test_public_readyz_scrubs_capability_details(client_factory, settings_override) -> None:
    """The public healthcheck returns booleans only: detail strings name internal env
    vars/filesystem errors and stay behind the authenticated /diagnostics path."""
    settings_override(SECURITY_SANDBOX="vercel")
    code, body = _readyz(client_factory())
    assert code == 200
    assert body["capabilities"]["sandbox"]["configured"] is False
    assert body["capabilities"]["sandbox"]["detail"] == ""  # no env-var names in public body
    # the same information remains available to the gated diagnostics body
    body = client_factory("ops-owner").get("/diagnostics").json()
    assert "VERCEL_TOKEN" in body["capabilities"]["sandbox"]["detail"]


def test_diagnostics_github_verify_uses_app_jwt(client_factory, monkeypatch) -> None:
    """The live check calls GET /app with the App JWT and reports only metadata."""
    import httpx

    from backend import ops

    class FakeApp:
        def __init__(self, *a, **k):
            self._client = httpx.Client()

        def _app_headers(self):
            return {"Authorization": "Bearer fake"}

    class Resp:
        status_code = 200

        def json(self):
            return {"slug": "ari-ai"}

    import backend.github

    monkeypatch.setattr(backend.github, "GitHubApp", FakeApp)
    monkeypatch.setattr(httpx.Client, "get", lambda self, *a, **k: Resp())
    ops._gh_verify_cache = None
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "x")
    from backend.settings import get_settings

    get_settings.cache_clear()
    out = ops._verify_github_app(get_settings())
    assert out == {"verified": True, "app_slug": "ari-ai"}


def test_sandbox_capability_details(settings_override) -> None:
    """vercel mode reports exactly which env vars are missing; local-dev gated to dev/test."""
    from backend import ops
    from backend.settings import get_settings

    settings_override(SECURITY_SANDBOX="vercel")
    get_settings.cache_clear()
    cap = ops._sandbox_capability(get_settings())
    assert cap["mode"] == "vercel" and cap["configured"] is False
    assert "VERCEL_TOKEN" in cap["detail"]

    settings_override(SECURITY_SANDBOX="local-dev", APP_ENV="production")
    get_settings.cache_clear()
    cap = ops._sandbox_capability(get_settings())
    assert cap["configured"] is False
