"""Manual e2e stack: real FastAPI + PostgreSQL + worker + Next.js, kept running
for an interactive browser walkthrough (no Playwright at the end).

GitHub seams a manual browser cannot page.route around are stubbed server-side:
- github_repo_access rows seed the /connect picker for the dev-login user.
- repositories.installation_id is set and backend.api.github_repos.GitHubApp is
  replaced with a fake serving scripted commits.
- backend.checkouts.ensure_checkout is a no-op so workers use the local
  REPOSITORY_BINDINGS_FILE checkout instead of an App fetch.


What is real: HTTP API, migrations, session cookies/CSRF, durable queue and worker,
pinned git checkout of the owned examples/payments-lite target, reviewer test
execution, local-dev sandbox checks, Patch/Verify, artifacts, SSE, the Next app.
What is scripted: model replies. The worker's single LLM gateway uses the same
TEST-ONLY transports as backend/tests/test_unified_assessment.py (Sai's reviewer
script + Alan's role scripts), so every agent run is persisted with
provider_mode="fake" and the UI must label it as not live. No API key is used.

Usage (from frontend/):  ../.venv/bin/python tests/e2e/stack.py [playwright args]
Env: TEST_DATABASE_URL (admin URL of an existing Postgres; default embedded pgserver),
     E2E_SKIP_BUILD=1 to reuse an existing `.next` build made with NEXT_PUBLIC_DEV_LOGIN=1.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa

FRONTEND = Path(__file__).resolve().parents[2]
ROOT = FRONTEND.parent
PAYMENTS = ROOT / "examples" / "payments-lite"
REPO_NAME = "acme/payments-lite"
WEBHOOK_SECRET = "e2e-webhook-secret-not-real"


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=e2e",
            "-c",
            "user.email=e2e@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        text=True,
    ).strip()


def _owned_repo(tmp: Path) -> tuple[Path, str]:
    repo = tmp / "owned-repo"
    shutil.copytree(
        PAYMENTS,
        repo,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.egg-info", ".venv"),
    )
    (repo / "labels.yaml").unlink(
        missing_ok=True
    )  # the answer key never ships with the assessed repo
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "payments-lite")
    return repo, _git(repo, "rev-parse", "HEAD")


def _admin_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return (
            url
            if not url.startswith("postgresql://")
            else "postgresql+psycopg://" + url[len("postgresql://") :]
        )
    from backend.devdb import start

    return start(ROOT / ".dev" / "pgtest", database="postgres")


def _wait(url: str, timeout: float = 60) -> None:
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"{url} did not become ready")


def main(argv: list[str]) -> int:
    admin_url = _admin_url()
    name = f"e2e_{uuid.uuid4().hex[:10]}"
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    tmp = Path(tempfile.mkdtemp(prefix="hackatl-e2e-"))
    server = thread = worker_thread = next_proc = helper = None
    fixture_stop: list[Any] = []
    stop = threading.Event()
    try:
        repo, sha = _owned_repo(tmp)
        bindings = tmp / "bindings.json"
        bindings.write_text("[]")
        api_port = _port()
        os.environ.update(
            APP_ENV="test",
            DATABASE_URL=sa.engine.make_url(admin_url)
            .set(database=name)
            .render_as_string(hide_password=False),
            ARTIFACT_STORE_URI=(tmp / "artifacts").as_uri(),
            AUTH_DEV_LOGIN_ENABLED="true",
            ANTHROPIC_API_KEY="",
            GITHUB_WEBHOOK_SECRET=WEBHOOK_SECRET,
            RATE_LIMIT_PER_MINUTE="100000",
            REPOSITORY_BINDINGS_FILE=str(bindings),
            SECURITY_SANDBOX="local-dev",
            SECURITY_AGENTS_WORKDIR=str(tmp / "work"),
            ORCHESTRATOR_FACTORY="security_agents.api:build_orchestrator",
            CODE_INTELLIGENCE_FACTORY="code_intelligence.api:build_service",
        )
        os.environ.pop("SECURITY_AGENTS_TARGET_DIR", None)
        from backend.settings import get_settings

        get_settings.cache_clear()
        from backend.migrate import upgrade

        upgrade()

        # TEST-ONLY scripted model transport (see module docstring).
        import code_review

        sys.path[:0] = [
            str(Path(code_review.__file__).resolve().parents[1] / "tests"),
            str(ROOT / "backend" / "tests"),
        ]
        from cr_fakes import fake  # type: ignore[import-not-found]
        from sa_bridge import AlanScriptTransport  # type: ignore[import-not-found]
        from test_reviewer_agent import (  # type: ignore[import-not-found]
            pl_repair_script,
            pl_review_script,
        )

        from backend import llm, worker

        # Reviewer turns, then its repair turns once the functional repair is approved.
        reviewer = [
            *pl_review_script(SimpleNamespace(repo=repo)),
            *pl_repair_script("target/tests/generated/test_cr_refund_bound.py"),
        ]
        bridge = AlanScriptTransport(repo / "target", fallback=fake(reviewer))
        llm.TRANSPORT_OVERRIDE = lambda: bridge
        # Several UI journeys execute independent assessments in this stack. Give
        # each one a fresh scripted conversation, retaining its real gateway hooks.
        # A single shared FakeTransport would exhaust the reviewer after run one.
        original_build_gateway = llm.build_gateway
        assessment_transports: dict[str, AlanScriptTransport] = {}

        def isolated_gateway(**kwargs: Any) -> llm.LLMGateway:
            owner = getattr(kwargs.get("charge"), "__self__", None)
            assessment_id = getattr(owner, "assessment_id", None)
            if assessment_id and kwargs.get("transport") is None:
                if assessment_id not in assessment_transports:
                    assessment_transports[assessment_id] = AlanScriptTransport(
                        repo / "target", fallback=fake(reviewer)
                    )
                kwargs["transport"] = assessment_transports[assessment_id]
            return original_build_gateway(**kwargs)

        llm.build_gateway = isolated_gateway

        import uvicorn

        from backend.main import create_app

        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", api_port))
        server = uvicorn.Server(uvicorn.Config(create_app(), log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        _wait(f"http://127.0.0.1:{api_port}/healthz")

        def work() -> None:
            while not stop.is_set():
                try:
                    if not worker.drain(max_jobs=10):
                        time.sleep(0.2)
                except Exception as exc:  # surface, keep serving so the UI shows the failure
                    print(f"[e2e worker] {type(exc).__name__}: {exc}", file=sys.stderr)
                    time.sleep(0.5)

        worker_thread = threading.Thread(target=work, daemon=True)
        worker_thread.start()

        # Operator setup the UI cannot do: two org owners, a project and the repo binding
        # (REPOSITORY_BINDINGS_FILE is an operator registry, not a user-facing API).
        import httpx

        from backend.db import defense_recommendations, github_repo_access, memberships, repositories, transaction

        base = f"http://127.0.0.1:{api_port}/api/v1"
        alice = httpx.Client(base_url=base)
        me = (
            alice.post("/auth/dev/login", json={"github_login": "e2e-alice"})
            .raise_for_status()
            .json()
        )
        bob = (
            httpx.Client(base_url=base)
            .post("/auth/dev/login", json={"github_login": "e2e-bob"})
            .raise_for_status()
            .json()
        )
        with transaction() as conn:
            conn.execute(
                sa.insert(memberships).values(
                    org_id=me["memberships"][0]["organization"]["id"],
                    user_id=bob["user"]["id"],
                    role="owner",
                )
            )
        alice.headers["X-CSRF-Token"] = me["csrf_token"]
        pid = (
            alice.post("/projects", json={"name": "payments-lite (e2e)", "settings": {"delivery_mode": "manual"}})
            .raise_for_status()
            .json()["id"]
        )
        rid = (
            alice.post(f"/projects/{pid}/repositories", json={"full_name": REPO_NAME})
            .raise_for_status()
            .json()["id"]
        )
        # The commit-workspace journey completes its own runs. Keep its findings/approvals
        # separate from the original journey's exact-count assertions.
        workspace_pid = (
            alice.post("/projects", json={"name": "commit-workspace (e2e)", "settings": {"delivery_mode": "manual"}})
            .raise_for_status()
            .json()["id"]
        )
        workspace_name = "acme/commit-workspace"
        workspace_rid = (
            alice.post(
                f"/projects/{workspace_pid}/repositories", json={"full_name": workspace_name}
            )
            .raise_for_status()
            .json()["id"]
        )
        bindings.write_text(
            json.dumps(
                [
                    {"repo_id": rid, "local_path": str(repo), "subdir": "."},
                    {"repo_id": workspace_rid, "local_path": str(repo), "subdir": "."},
                ]
            )
        )

        # Manual walkthrough seams (module docstring): picker rows + commit list.
        INSTALLATION_ID = 4242
        with transaction() as conn:
            for fn in (REPO_NAME, workspace_name):
                conn.execute(
                    sa.insert(github_repo_access).values(
                        user_id=me["user"]["id"],
                        full_name=fn,
                        installation_id=INSTALLATION_ID,
                        default_branch="main",
                        private=True,
                    )
                )
            for rid_ in (rid, workspace_rid):
                conn.execute(
                    sa.update(repositories)
                    .where(repositories.c.id == rid_)
                    .values(installation_id=INSTALLATION_ID)
                )

        class FakeGitHubApp:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            def recent_commits(
                self, installation_id: int, full_name: str, branch: str, limit: int = 20
            ) -> list[dict[str, Any]]:
                other = "9" * 39 + "f"
                return [
                    {
                        "sha": sha,
                        "message": "Selected payment change",
                        "author": "e2e-alice",
                        "committed_at": "2026-10-20T12:00:00Z",
                        "html_url": f"https://github.com/{full_name}/commit/{sha}",
                    },
                    {
                        "sha": other,
                        "message": "A different commit",
                        "author": "test-author",
                        "committed_at": "2026-10-19T12:00:00Z",
                        "html_url": f"https://github.com/{full_name}/commit/{other}",
                    },
                ]

        import backend.api.github_repos as gh_api

        gh_api.GitHubApp = FakeGitHubApp  # type: ignore[assignment,misc]

        import backend.checkouts as checkouts

        checkouts.ensure_checkout = lambda *a, **k: None  # type: ignore[assignment]
        alice.close()

        # Stored simulation-derived recommendation for Live Defense e2e (no live RL
        # inference in this stack). Same persistence path the worker uses; labelled
        # simulation_derived. Not mixed with layout-preview fixtures.
        from shared import examples
        from shared.ids import IdPrefix, new_id
        from shared.rl import FeatureContribution, RLExplanation

        rec0 = examples.build()["DefenseRecommendation"].model_dump(mode="json")
        rec_id = new_id(IdPrefix.rec)
        node_name = (
            rec0.get("node")
            if rec0.get("node") in ("gateway", "api", "auth", "payments", "db")
            else None
        )
        explanation = RLExplanation(
            recommendation_id=rec_id,
            action=str(rec0["action"]),
            runner_up="noop",
            margin=float(rec0["q_margin"]),
            method="occlusion",
            top_contributions=[
                FeatureContribution(
                    node=node_name,
                    feature="auth_failures",
                    value=0.81,
                    delta_margin=0.31,
                    kind="signal",
                )
            ],
            caveat="Occlusion of simulation-trained features; not real-world causality.",
        ).model_dump(mode="json")
        with transaction() as conn:
            conn.execute(
                sa.insert(defense_recommendations).values(
                    id=rec_id,
                    project_id=pid,
                    doc={**rec0, "id": rec_id, "project_id": pid},
                    explanation=explanation,
                )
            )

        owned_test_port = _port()
        helper_port = _port()

        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class OwnedTestHelper(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/start":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                secret = str(body.get("secret") or "")
                if not secret.startswith("hkt_ctl_"):
                    self.send_response(400)
                    self.end_headers()
                    return
                from backend.live_defense.owned_test_app import create_app, serve_in_thread

                while fixture_stop:
                    fixture_stop.pop()()
                _app = create_app(secret)
                bound, stop = serve_in_thread(_app, port=owned_test_port)
                fixture_stop.append(stop)
                payload = json.dumps(
                    {"port": bound, "environment": "OWNED TEST ENVIRONMENT"}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        helper = ThreadingHTTPServer(("127.0.0.1", helper_port), OwnedTestHelper)
        helper_thread = threading.Thread(target=helper.serve_forever, daemon=True)
        helper_thread.start()

        web_port = _port()
        os.environ.setdefault(
            "DEMO_ACCESS_CODE", "e2e-demo-access-code"
        )  # demo account gate (proxy.ts)
        env = {
            **os.environ,
            "BACKEND_ORIGIN": f"http://127.0.0.1:{api_port}",
            "NEXT_PUBLIC_DEV_LOGIN": "1",
        }
        env.pop("NODE_OPTIONS", None)
        if os.environ.get("E2E_SKIP_BUILD") != "1":
            subprocess.run(["npx", "next", "build"], cwd=FRONTEND, env=env, check=True)
        next_proc = subprocess.Popen(
            ["npx", "next", "start", "--port", str(web_port), "--hostname", "127.0.0.1"],
            cwd=FRONTEND,
            env=env,
        )
        _wait(f"http://127.0.0.1:{web_port}/lab/bundles")

        print(f"[manual stack] READY", flush=True)
        print(f"[manual stack] web:     http://127.0.0.1:{web_port}", flush=True)
        print(f"[manual stack] api:     {base}", flush=True)
        print(f"[manual stack] commit:  {sha}", flush=True)
        print(f"[manual stack] project: {pid} repo: {rid} ({REPO_NAME})", flush=True)
        print(f"[manual stack] project: {workspace_pid} repo: {workspace_rid} ({workspace_name})", flush=True)
        print(f"[manual stack] rec_id:  {rec_id}", flush=True)
        print(f"[manual stack] dev login: e2e-alice (or e2e-bob)", flush=True)
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        stop.set()
        if helper is not None:
            helper.shutdown()
        while fixture_stop:
            fixture_stop.pop()()
        if next_proc:
            next_proc.terminate()
            try:
                next_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                next_proc.kill()
        if server:
            server.should_exit = True
        if thread:
            thread.join(timeout=10)
        if worker_thread:
            worker_thread.join(timeout=10)
        if "backend.db" in sys.modules:
            from backend.db import get_engine

            get_engine().dispose()
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
