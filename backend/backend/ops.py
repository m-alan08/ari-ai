"""Operational diagnostics: /readyz and /diagnostics data.

/readyz answers "can this service hold traffic" (database + queue, plus worker liveness
when the deployment runs workers). The capability block answers "could an assessment
actually run" — live-model configuration, GitHub App credentials, webhook secret,
sandbox capability, artifact-store writability — using only metadata, never values.

/diagnostics goes one step further and *verifies* what is safe to verify on demand:
the GitHub App JWT against ``GET /app``, the sandbox SDK import, job-queue counts,
worker heartbeat rows, and the applied migration revision. External calls are bounded
and cached so the endpoint stays cheap and does not flap the deploy healthcheck.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from backend import jobs
from backend.artifacts import ArtifactStore
from backend.db import get_engine
from backend.llm import llm_mode
from backend.settings import Settings
from shared.base import utcnow


def _sandbox_capability(s: Settings) -> dict[str, Any]:
    """Is the configured sandbox mode actually usable here? Metadata only."""
    mode = s.security_sandbox
    if mode == "local-dev":
        ok = s.is_dev
        return {
            "mode": mode,
            "configured": ok,
            "detail": "dev/test only" if ok else "SANDBOX_POLICY_VIOLATION outside dev/test",
        }
    if mode == "vercel":
        missing = [
            name
            for name in ("VERCEL_TOKEN", "VERCEL_PROJECT_ID", "VERCEL_TEAM_ID")
            if not os.environ.get(name)
        ]
        return {
            "mode": mode,
            "configured": not missing,
            "detail": "" if not missing else f"missing env: {', '.join(missing)}",
        }
    # docker
    return {
        "mode": mode,
        "configured": shutil.which("docker") is not None,
        "detail": "" if shutil.which("docker") else "docker binary not on PATH",
    }


def _artifact_writable() -> tuple[bool, str]:
    """Write+read+delete a probe under the artifact root (no DB write, no secrets)."""
    try:
        store = ArtifactStore()
        store.root.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=store.root, prefix=".readyz-probe-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(b"readyz")
            if Path(probe).read_bytes() != b"readyz":
                return False, "probe read-back mismatch"
        finally:
            Path(probe).unlink(missing_ok=True)
        return True, ""
    except Exception as exc:  # noqa: BLE001 - report, never crash readiness
        return False, f"{type(exc).__name__}: {exc}"[:200]


def _llm_capability(s: Settings) -> dict[str, Any]:
    mode = llm_mode(s)
    return {
        "mode": mode,
        "model": s.model_for("assessment"),
        "configured": mode in ("live", "fake"),
    }


def capabilities(
    s: Settings, *, artifact_probe: bool = True, include_details: bool = True
) -> dict[str, Any]:
    """The full capability block, all local checks (no outbound calls).

    ``include_details=False`` scrubs the free-text ``detail`` fields (they name internal
    env vars and filesystem errors) — that is the shape the public /readyz returns."""
    key = s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else ""
    github_ok = bool(
        s.github_app_id and (s.github_app_private_key_path or s.github_app_private_key)
    )
    webhook_ok = bool(
        s.github_webhook_secret
        and s.github_webhook_secret.get_secret_value() not in ("", "replace-me")
    )
    writable, detail = (True, "probe skipped") if not artifact_probe else _artifact_writable()
    caps = {
        "llm": _llm_capability(s),
        "github": {
            "app_configured": github_ok,
            "oauth_configured": bool(s.github_oauth_client_id and s.github_oauth_client_secret),
            "webhook_secret_configured": webhook_ok,
        },
        "sandbox": _sandbox_capability(s),
        "artifact_store": {"writable": writable, "detail": detail},
        "anthropic_key_configured": bool(key and key != "replace-me"),
    }
    if not include_details:
        for cap in caps.values():
            if isinstance(cap, dict) and "detail" in cap:
                cap["detail"] = ""
    return caps


def require_workers(s: Settings) -> bool:
    if s.readyz_require_workers is not None:
        return s.readyz_require_workers
    return s.app_env in ("production", "staging")


def workers_status(s: Settings, conn: sa.engine.Connection) -> dict[str, Any]:
    rows = jobs.live_workers(conn, s.worker_liveness_s)
    counts = jobs.queue_counts(conn)
    expected = int(os.environ.get("WORKER_PROCESSES") or 0) or None
    freshest = None
    if rows:
        age = (utcnow() - max(r["heartbeat_at"] for r in rows)).total_seconds()
        freshest = round(max(0.0, age), 1)
    return {
        "alive": len(rows),
        "expected": expected,
        "busy": sum(1 for r in rows if r["current_job_id"]),
        "queued_jobs": counts["queued"],
        "running_jobs": counts["running"],
        "freshest_heartbeat_age_s": freshest,
        "required": require_workers(s),
    }


def readiness(s: Settings, *, include_details: bool = False) -> tuple[dict[str, Any], int]:
    """Body for /readyz. HTTP 503 only when the service cannot hold traffic at all.

    ``include_details=True`` (the authenticated /diagnostics path) keeps capability
    ``detail`` strings; the public healthcheck gets booleans only."""
    db_ok = queue_ok = False
    workers: dict[str, Any] = {
        "alive": 0,
        "expected": None,
        "busy": 0,
        "queued_jobs": 0,
        "running_jobs": 0,
        "freshest_heartbeat_age_s": None,
        "required": require_workers(s),
    }
    caps: dict[str, Any] = {}
    try:
        with get_engine().begin() as conn:
            conn.execute(sa.text("SELECT 1"))
            db_ok = True
            queue_ok = jobs.queue_reachable(conn)
            workers = workers_status(s, conn)
        caps = capabilities(s, include_details=include_details)
    except Exception:
        import logging

        logging.getLogger("backend.api").exception("readiness check failed")
        caps = {
            "llm": {
                "mode": llm_mode(s),
                "model": s.model_for("assessment"),
                "configured": llm_mode(s) in ("live", "fake"),
            },
            "github": {
                "app_configured": False,
                "oauth_configured": False,
                "webhook_secret_configured": False,
            },
            "sandbox": {
                "mode": s.security_sandbox,
                "configured": False,
                "detail": "unchecked (db down)",
            },
            "artifact_store": {"writable": False, "detail": "unchecked (db down)"},
            "anthropic_key_configured": False,
        }
        if not include_details:
            for cap in caps.values():
                if isinstance(cap, dict) and "detail" in cap:
                    cap["detail"] = ""
    workers_ok = workers["alive"] >= 1 if workers["required"] else True
    ready = db_ok and queue_ok and workers_ok
    caps_ready = bool(caps) and all(
        [
            caps["llm"]["configured"],
            caps["github"]["app_configured"],
            caps["github"]["webhook_secret_configured"],
            caps["sandbox"]["configured"],
            caps["artifact_store"]["writable"],
            workers_ok,
        ]
    )
    status = "ready" if ready else ("not_ready" if not (db_ok and queue_ok) else "degraded")
    body = {
        "status": status,
        "database": db_ok,
        "queue": queue_ok,
        "workers": workers,
        "capabilities": caps,
        "capabilities_ready": caps_ready,
        "llm_mode": caps["llm"]["mode"],
        "github_app_configured": caps["github"]["app_configured"],
        "webhook_secret_configured": caps["github"]["webhook_secret_configured"],
        "git_sha": s.deployed_git_sha,
        "schema_version": _schema_version(),
    }
    return body, 200 if ready else 503


def _schema_version() -> str:
    from shared import SCHEMA_VERSION

    return SCHEMA_VERSION


_GH_VERIFY_TTL_S = 60.0
_gh_verify_lock = threading.Lock()
_gh_verify_cache: tuple[float, dict[str, Any]] | None = None


def _verify_github_app(s: Settings) -> dict[str, Any]:
    """Mint the App JWT and call GET /app: proves the private key is valid and the app
    exists, returning only the app slug (never tokens or key material). Cached 60 s."""
    global _gh_verify_cache
    with _gh_verify_lock:
        if _gh_verify_cache and time.monotonic() - _gh_verify_cache[0] < _GH_VERIFY_TTL_S:
            return _gh_verify_cache[1]
        result: dict[str, Any]
        try:
            if not (
                s.github_app_id and (s.github_app_private_key or s.github_app_private_key_path)
            ):
                result = {"verified": False, "detail": "not configured"}
            else:
                import httpx

                from backend.github import GitHubApp

                app = GitHubApp(settings=s, client=httpx.Client(timeout=8.0))
                r = app._client.get("/app", headers=app._app_headers())
                if r.status_code == 200:
                    result = {"verified": True, "app_slug": r.json().get("slug")}
                else:
                    result = {"verified": False, "detail": f"GET /app → {r.status_code}"}
        except Exception as exc:  # noqa: BLE001 - classify, never leak internals
            result = {"verified": False, "detail": f"{type(exc).__name__}"}
        _gh_verify_cache = (time.monotonic(), result)
        return result


def _migration_state() -> dict[str, Any]:
    from alembic.script import ScriptDirectory

    from backend.migrate import alembic_config

    heads = sorted(ScriptDirectory.from_config(alembic_config()).get_heads())
    try:
        with get_engine().begin() as conn:
            current = (
                conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalars().all()
            )
    except Exception as exc:  # noqa: BLE001
        return {
            "expected_head": heads[-1] if heads else None,
            "applied": [],
            "pending": True,
            "detail": type(exc).__name__,
        }
    return {
        "expected_head": heads[-1] if heads else None,
        "applied": sorted(str(v) for v in current),
        "pending": sorted(set(heads) - {str(v) for v in current}),
    }


def diagnostics(s: Settings) -> dict[str, Any]:
    """Body for GET /diagnostics: everything /readyz reports plus live verification.

    Authenticated owners only (main.py gates it): worker ids, queue counts, migration
    state and capability details are internal infrastructure metadata."""
    body, _ = readiness(s, include_details=True)
    try:
        with get_engine().begin() as conn:
            job_counts = jobs.queue_counts(conn)
            workers = jobs.live_workers(conn, s.worker_liveness_s)
    except Exception:
        job_counts, workers = {}, []
    body["capabilities"]["github"]["verified"] = _verify_github_app(s)
    # /diagnostics keeps the capability detail strings /readyz scrubs.
    sandbox = dict(body["capabilities"]["sandbox"])
    if s.security_sandbox == "vercel":
        try:
            import vercel.sandbox  # noqa: F401

            sandbox["sdk_importable"] = True
        except Exception as exc:  # noqa: BLE001
            sandbox["sdk_importable"] = False
            sandbox["detail"] = (sandbox.get("detail") or f"vercel.sandbox: {type(exc).__name__}")[
                :200
            ]
    elif s.security_sandbox == "docker" and shutil.which("docker"):
        import subprocess

        try:
            subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=True)
            sandbox["daemon_reachable"] = True
        except Exception:
            sandbox["daemon_reachable"] = False
    body["capabilities"]["sandbox"] = sandbox
    body["migrations"] = _migration_state()
    body["jobs"] = job_counts
    now = utcnow()
    body["worker_rows"] = [
        {
            "worker_id": w["worker_id"],
            "heartbeat_at": w["heartbeat_at"].isoformat(),
            "current_job_id": w["current_job_id"],
            # Seconds since the worker's main claim loop last polled. A large age while
            # current_job_id is set means the handler has been running that long — the
            # "stopped processing" surface a bare heartbeat cannot show.
            "last_poll_age_s": round(max(0.0, (now - w["last_poll_at"]).total_seconds()), 1)
            if w.get("last_poll_at")
            else None,
        }
        for w in workers
    ]
    body["generated_at"] = utcnow().isoformat()
    return body
