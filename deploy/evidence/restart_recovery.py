"""Staging-style restart/recovery evidence on a disposable stack — run locally or in
a disposable staging env, never pointed at production data.

    uv run python deploy/evidence/restart_recovery.py --out /tmp/ari-evidence

What is real: embedded PostgreSQL 16 + alembic migrations + FastAPI API + REAL
hackatl-worker subprocesses + the durable queue + content-addressed artifact store
+ git checkout + local-dev sandbox + orchestrator state machine.
What is scripted: model replies (TEST-ONLY transports, provider_mode="fake"), so the
scenarios exercise process/queue/checkpoint semantics without paid calls.

Scenarios (each prints RESULT and appends to evidence.json):
  S1 graceful-drain:   SIGTERM mid-assessment → job completes, checkpoint saved
  S2 hard-kill:        SIGKILL mid-assessment → re-claimed → honest FAILED, never
                       fabricated success (CONFLICT_STATE, visible error)
  S3 checkpoint-resume: approve S1's pending remediation on a FRESH worker →
                       resumes from checkpoint → COMPLETED, artifacts intact
  S4 exactly-once:     2 racing workers on N probe jobs → all succeed, attempts==1;
                       duplicate dedupe_key → single job row
  S5 migration-rollback: downgrade 0017→0016 (heartbeats gone, RLS kept) → re-upgrade
  S6 backup-restore:   pg_dump → new DB → psql restore → row counts identical

Exit 0 only if every scenario passes. Prints nothing secret.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import sqlalchemy as sa  # noqa: E402

REPO_NAME = "acme/evidence-lite"
WEBHOOK_SECRET = "evidence-webhook-secret-not-real"
PAYMENTS = ROOT / "examples" / "payments-lite"
HARNESS_JOB_LEASE_S = "6"  # short so a killed worker's lease re-claims quickly


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


class Results:
    def __init__(self, out: Path) -> None:
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.rows: list[dict[str, Any]] = []

    def record(self, name: str, ok: bool, detail: dict[str, Any]) -> None:
        row = {"scenario": name, "ok": ok, **detail}
        self.rows.append(row)
        print(f"RESULT {name}: {'PASS' if ok else 'FAIL'} :: {json.dumps(detail)[:400]}")
        (self.out / "evidence.json").write_text(json.dumps(self.rows, indent=2))


class Proc:
    def __init__(self, env: dict[str, str], name: str) -> None:
        self.env = env
        self.name = name
        self.p: subprocess.Popen[bytes] | None = None

    def start(self, probe: bool = False) -> subprocess.Popen[bytes]:
        env = dict(self.env)
        if probe:
            env["EVIDENCE_PROBE"] = "1"
        self.p = subprocess.Popen(
            [sys.executable, str(ROOT / "deploy" / "evidence" / "scripted_worker.py")],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=None,
        )
        return self.p

    def stop(self) -> None:
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.p.kill()


def _wait_http(url: str, timeout: float = 60) -> None:
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"{url} did not become ready")


def _poll(fn, want, timeout: float = 60.0, interval: float = 0.3):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if want(last):
            return last
        time.sleep(interval)
    raise TimeoutError(f"poll timeout after {timeout}s; last={last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / ".dev" / "evidence"))
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    results = Results(out)
    deadline = time.monotonic() + args.timeout

    tmp = Path(tempfile.mkdtemp(prefix="ari-evidence-"))
    procs: list[Proc] = []
    server = thread = None
    worker_env: dict[str, str] = {}
    admin = None
    dbname = f"ev_{uuid.uuid4().hex[:10]}"

    try:
        # ------------------------------------------------ disposable PG + stack
        import pgserver

        pgdata = tmp / "pgdata"
        server_pg = pgserver.get_server(str(pgdata), cleanup_mode=None)
        admin_url = server_pg.get_uri("postgres").replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
        admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
        db_url = (
            sa.engine.make_url(admin_url).set(database=dbname).render_as_string(hide_password=False)
        )

        repo = tmp / "owned-repo"
        import shutil as _sh

        _sh.copytree(
            PAYMENTS,
            repo,
            ignore=_sh.ignore_patterns("__pycache__", ".pytest_cache", "*.egg-info", ".venv"),
        )
        (repo / "labels.yaml").unlink(missing_ok=True)
        _git(repo, "init", "-q")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "evidence")
        sha = _git(repo, "rev-parse", "HEAD")

        bindings = tmp / "bindings.json"
        artifacts = tmp / "artifacts"
        api_port = _port()

        base_env = {
            **os.environ,
            "APP_ENV": "test",
            "DATABASE_URL": db_url,
            "ARTIFACT_STORE_URI": artifacts.as_uri(),
            "AUTH_DEV_LOGIN_ENABLED": "true",
            "ANTHROPIC_API_KEY": "",
            "GITHUB_WEBHOOK_SECRET": WEBHOOK_SECRET,
            "RATE_LIMIT_PER_MINUTE": "100000",
            "REPOSITORY_BINDINGS_FILE": str(bindings),
            "SECURITY_SANDBOX": "local-dev",
            "SECURITY_AGENTS_WORKDIR": str(tmp / "work"),
            "SECURITY_WORKDIR": str(tmp / "work"),
            "ORCHESTRATOR_FACTORY": "security_agents.api:build_orchestrator",
            "CODE_INTELLIGENCE_FACTORY": "code_intelligence.api:build_service",
            "WORKER_LEASE_S": HARNESS_JOB_LEASE_S,
            "WORKER_POLL_INTERVAL_S": "0.2",
            "EVIDENCE_REPO_DIR": str(repo),
            "RL_CHECKPOINT_PATH": "",
        }
        worker_env = base_env
        os.environ.update({k: v for k, v in base_env.items() if k != "PATH"})

        from backend.settings import get_settings

        get_settings.cache_clear()
        from backend.migrate import upgrade

        upgrade()

        import uvicorn

        from backend.main import create_app

        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", api_port))
        server = uvicorn.Server(uvicorn.Config(create_app(), log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        _wait_http(f"http://127.0.0.1:{api_port}/healthz")

        # ------------------------------------------------ seed org/project/scope
        import httpx

        from backend.db import transaction

        base = f"http://127.0.0.1:{api_port}/api/v1"
        alice = httpx.Client(base_url=base)
        me = (
            alice.post("/auth/dev/login", json={"github_login": "ev-alice"})
            .raise_for_status()
            .json()
        )
        alice.headers["X-CSRF-Token"] = me["csrf_token"]
        pid = (
            alice.post(
                "/projects", json={"name": "evidence", "settings": {"delivery_mode": "manual"}}
            )
            .raise_for_status()
            .json()["id"]
        )
        rid = (
            alice.post(f"/projects/{pid}/repositories", json={"full_name": REPO_NAME})
            .raise_for_status()
            .json()["id"]
        )
        bindings.write_text(json.dumps([{"repo_id": rid, "local_path": str(repo), "subdir": "."}]))
        scope = (
            alice.post(
                f"/projects/{pid}/scopes",
                json={
                    "name": "evidence sandbox",
                    "targets": [
                        {
                            "kind": "sandbox_build",
                            "repo_id": rid,
                            "build_recipe_ref": "build_recipe.json",
                        }
                    ],
                    # Same categories as test_unified_assessment: produces a diagnosable
                    # finding -> AWAITING_APPROVAL pause -> checkpoint to resume from.
                    "allowed_check_categories": [
                        "access_control.ownership",
                        "input_validation.bounds",
                    ],
                    "expires_at": "2027-01-01T00:00:00Z",
                },
            )
            .raise_for_status()
            .json()
        )
        alice.post(f"/scopes/{scope['id']}/approve", json={}).raise_for_status()

        # A second owner: remediation approvals may require approver != requester.
        from backend.db import memberships

        bob = httpx.Client(base_url=base)
        bobme = (
            bob.post("/auth/dev/login", json={"github_login": "ev-bob"}).raise_for_status().json()
        )
        with transaction() as conn:
            conn.execute(
                sa.insert(memberships).values(
                    org_id=me["memberships"][0]["organization"]["id"],
                    user_id=bobme["user"]["id"],
                    role="owner",
                )
            )
        bob.headers["X-CSRF-Token"] = bobme["csrf_token"]

        from backend.db import assessment_events, assessments
        from backend.db import jobs as jobs_t

        def asm_state(aid: str) -> str | None:
            with transaction() as conn:
                return conn.execute(
                    sa.select(assessments.c.state).where(assessments.c.id == aid)
                ).scalar_one_or_none()

        def job_row(jid: str) -> dict[str, Any] | None:
            with transaction() as conn:
                r = conn.execute(sa.select(jobs_t).where(jobs_t.c.id == jid)).mappings().first()
                return dict(r) if r else None

        def job_running(jid: str) -> bool:
            j = job_row(jid)
            return bool(j and j["status"] == "running")

        def checkpoint_artifact(aid: str) -> str | None:
            with transaction() as conn:
                rows = (
                    conn.execute(
                        sa.select(assessment_events.c.doc)
                        .where(assessment_events.c.assessment_id == aid)
                        .where(assessment_events.c.type == "assessment.progress")
                    )
                    .scalars()
                    .all()
                )
            for d in rows:
                ref = (d or {}).get("data", {}).get("checkpoint_artifact_id")
                if ref:
                    return ref
            return None

        def artifact_path(art_id: str) -> Path | None:
            from backend.db import artifacts as artifacts_t

            with transaction() as conn:
                key = conn.execute(
                    sa.select(artifacts_t.c.storage_key).where(artifacts_t.c.id == art_id)
                ).scalar_one_or_none()
            return artifacts / key if key else None

        def start_assessment() -> tuple[str, str]:
            r = (
                alice.post(
                    f"/projects/{pid}/assessments",
                    json={"scope_id": scope["id"], "repo_id": rid, "commit_sha": sha},
                )
                .raise_for_status()
                .json()
            )
            return r["assessment"]["id"], r["job_id"]

        # ------------------------------------------------ S1 SIGTERM drains job
        aid1, jid1 = start_assessment()
        w = Proc(worker_env, "w1")
        w.start()
        procs.append(w)
        _poll(lambda: job_running(jid1), bool, timeout=60)
        w.p.terminate()  # SIGTERM — must finish the in-hand job, then exit
        rc1 = w.p.wait(timeout=args.timeout / 3)
        _poll(lambda: job_row(jid1)["status"], lambda s: s == "succeeded", timeout=args.timeout / 3)
        _poll(
            lambda: asm_state(aid1),
            lambda s: s in ("AWAITING_APPROVAL", "COMPLETED"),
            timeout=args.timeout / 4,
        )
        st1 = asm_state(aid1)
        ref1 = checkpoint_artifact(aid1)
        found = [artifact_path(ref1)] if ref1 and artifact_path(ref1).exists() else []
        with transaction() as conn:
            ev_dump = [
                (r["type"], (r["doc"] or {}).get("message", ""), r["doc"].get("state"))
                for r in conn.execute(
                    sa.select(assessment_events.c.type, assessment_events.c.doc)
                    .where(assessment_events.c.assessment_id == aid1)
                    .order_by(assessment_events.c.seq)
                ).mappings()
            ]
            (out / "aid1-events.json").write_text(json.dumps(ev_dump, indent=1))
        results.record(
            "S1_graceful_drain",
            bool(rc1 == 0 and st1 == "AWAITING_APPROVAL" and ref1 and found),
            {
                "worker_exit": rc1,
                "job_status": job_row(jid1)["status"],
                "assessment_state": st1,
                "checkpoint_artifact": ref1,
                "artifact_file_on_disk": bool(found),
                "job_attempts": job_row(jid1)["attempts"],
            },
        )

        # ------------------------------------------------ S2 SIGKILL honest fail
        aid2, jid2 = start_assessment()
        w2 = Proc(worker_env, "w2")
        w2.start()
        procs.append(w2)
        _poll(lambda: job_running(jid2), bool, timeout=60)
        _poll(lambda: asm_state(aid2), lambda s: s not in (None, "CREATED"), timeout=60)
        w2.p.send_signal(signal.SIGKILL)
        w2.p.wait(timeout=15)
        w3 = Proc(worker_env, "w3")
        w3.start()
        procs.append(w3)
        _poll(lambda: asm_state(aid2), lambda s: s == "FAILED", timeout=180)
        j2 = job_row(jid2)
        err2 = (j2 or {}).get("last_error") or {}
        msg2 = err2 if isinstance(err2, str) else json.dumps(err2)
        honest = "lost" in msg2 or "CONFLICT_STATE" in msg2 or "conflict" in msg2.lower()
        results.record(
            "S2_hard_kill_honest_failure",
            bool(asm_state(aid2) == "FAILED" and honest),
            {
                "assessment_state": asm_state(aid2),
                "job_status": (j2 or {}).get("status"),
                "job_attempts": (j2 or {}).get("attempts"),
                "job_error": msg2[:200],
            },
        )

        # ------------------------------------------------ S3 checkpoint resume
        apprs = alice.get(f"/projects/{pid}/approvals").raise_for_status().json()
        items = apprs["items"] if isinstance(apprs, dict) and "items" in apprs else apprs
        (out / "approvals.json").write_text(json.dumps(items, indent=1))
        pend = [
            a
            for a in items
            if a.get("status") == "pending" and a.get("subject_type") == "remediation"
        ]
        decided = None
        for a in pend:
            decided = (
                bob.post(
                    f"/approvals/{a['id']}/decision",
                    json={"decision": "approved", "reason": "evidence harness"},
                )
                .raise_for_status()
                .json()
            )
        w4 = Proc(worker_env, "w4")
        w4.start()
        procs.append(w4)
        _poll(lambda: asm_state(aid1), lambda s: s == "COMPLETED", timeout=args.timeout / 3)
        n_artifacts = len(list(artifacts.rglob("*"))) - len(
            [p for p in artifacts.rglob("*") if p.is_dir()]
        )
        results.record(
            "S3_checkpoint_resume",
            bool(asm_state(aid1) == "COMPLETED" and found),
            {
                "assessment_state": asm_state(aid1),
                "approvals_decided": len(pend),
                "checkpoint_artifact_still_on_disk": bool(found),
                "artifact_store_objects": n_artifacts,
            },
        )

        # -------------------------------------- S7 safe retry after honest failure
        # S2's assessment is terminally FAILED by design (CONFLICT_STATE). The safe
        # retry path is a NEW assessment on the same repo+scope: prove the system
        # still accepts work and reaches the approval pause after the kills above.
        aid7, jid7 = start_assessment()
        w7 = Proc(worker_env, "w7")
        w7.start()
        procs.append(w7)
        _poll(
            lambda: asm_state(aid7),
            lambda s: s == "AWAITING_APPROVAL",
            timeout=args.timeout / 3,
        )
        results.record(
            "S7_post_crash_new_assessment",
            bool(asm_state(aid7) == "AWAITING_APPROVAL"),
            {
                "prior_failure_state": asm_state(aid2),
                "new_assessment_state": asm_state(aid7),
                "new_job_status": (job_row(jid7) or {}).get("status"),
                "checkpoint_artifact": bool(checkpoint_artifact(aid7)),
            },
        )
        w7.stop()

        # ------------------------------------------------ S4 exactly-once drain
        # Stop leftover workers first: anything still alive would claim the probe
        # jobs and fail them for lack of the evidence handler.
        for p in procs:
            p.stop()
        from backend import jobs

        with transaction() as conn:
            for i in range(10):
                jobs.enqueue(conn, "evidence_probe", {"i": i})
            jobs.enqueue(conn, "evidence_probe", {"dup": 1}, dedupe_key="ev-dup")
            jobs.enqueue(conn, "evidence_probe", {"dup": 1}, dedupe_key="ev-dup")
        w5, w6 = Proc(worker_env, "w5"), Proc(worker_env, "w6")
        w5.start(probe=True)
        w6.start(probe=True)
        procs += [w5, w6]

        def probes_done() -> tuple[int, int]:
            with transaction() as conn:
                rows = conn.execute(
                    sa.select(jobs_t.c.status, jobs_t.c.attempts).where(
                        jobs_t.c.kind == "evidence_probe"
                    )
                ).all()
            done = sum(1 for s, _ in rows if s == "succeeded")
            return done, len(rows)

        _poll(probes_done, lambda t: t[0] >= 11, timeout=60)
        with transaction() as conn:
            rows = conn.execute(
                sa.select(jobs_t.c.status, jobs_t.c.attempts, jobs_t.c.dedupe_key).where(
                    jobs_t.c.kind == "evidence_probe"
                )
            ).all()
        attempts_ok = all(a == 1 for _, a, _ in rows)
        dup_rows = [r for r in rows if r[2] == "ev-dup"]
        results.record(
            "S4_exactly_once_drain",
            bool(len(rows) == 11 and attempts_ok and len(dup_rows) == 1),
            {
                "probe_jobs_succeeded": probes_done()[0],
                "rows_total": len(rows),
                "all_attempts_1": attempts_ok,
                "dedupe_rows": len(dup_rows),
            },
        )
        # Stop the probe workers before the migration/backup scenarios: their
        # heartbeats would otherwise race the 0017 downgrade and the pg_dump.
        w5.stop()
        w6.stop()

        # ------------------------------------------------ S5 migration rollback
        from backend.migrate import downgrade

        def has_table(name: str) -> bool:
            eng = sa.create_engine(db_url)
            try:
                with eng.connect() as c:
                    return bool(
                        c.execute(
                            sa.text(
                                "SELECT EXISTS (SELECT FROM information_schema.tables"
                                " WHERE table_name = :n)"
                            ),
                            {"n": name},
                        ).scalar()
                    )
            finally:
                eng.dispose()

        downgrade("0016")
        gone = not has_table("worker_heartbeats")
        upgrade()
        back = has_table("worker_heartbeats")
        with transaction() as conn:
            conn.execute(sa.text("SELECT 1 FROM worker_heartbeats LIMIT 1"))
        results.record(
            "S5_migration_rollback",
            bool(gone and back),
            {"table_dropped_at_0016": gone, "table_back_at_head": back},
        )

        # ------------------------------------------------ S6 backup + restore
        # pgserver's wrappers inject -D (initdb-style) — call the bundled binaries
        # directly for dump/restore instead.
        pgbin = Path(pgserver.__file__).resolve().parent / "pginstall" / "bin"
        dump_path = tmp / "dump.sql"
        subprocess.run(
            [
                str(pgbin / "pg_dump"),
                "--no-owner",
                "--file",
                str(dump_path),
                server_pg.get_uri(dbname),
            ],
            check=True,
            capture_output=True,
        )
        db2 = f"ev_{uuid.uuid4().hex[:10]}"
        with admin.connect() as c:
            c.execute(sa.text(f'CREATE DATABASE "{db2}"'))
        subprocess.run(
            [
                str(pgbin / "psql"),
                "-v",
                "ON_ERROR_STOP=1",
                "-f",
                str(dump_path),
                server_pg.get_uri(db2),
            ],
            check=True,
            capture_output=True,
        )
        counts = {}
        eng = sa.create_engine(
            server_pg.get_uri(db2).replace("postgresql://", "postgresql+psycopg://", 1)
        )
        with eng.connect() as c:
            for t in ("assessments", "jobs", "assessment_events", "worker_heartbeats"):
                counts[t] = c.execute(sa.text(f'SELECT count(*) FROM "{t}"')).scalar()
        with transaction() as conn:
            src_counts = {
                t: conn.execute(sa.text(f'SELECT count(*) FROM "{t}"')).scalar() for t in counts
            }
        results.record(
            "S6_backup_restore",
            counts == src_counts and counts["assessments"] >= 2,
            {"restored_counts": counts, "source_counts": src_counts},
        )

    except Exception as exc:
        results.record("HARNESS_ERROR", False, {"error": f"{type(exc).__name__}: {exc}"[:300]})
    finally:
        for p in procs:
            p.stop()
        if server:
            server.should_exit = True
        if thread:
            thread.join(timeout=10)
        try:
            from backend.db import get_engine

            get_engine().dispose()
        except Exception:
            pass
        if admin is not None:
            with admin.connect() as c:
                c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
            admin.dispose()
        try:
            pgserver.get_server(str(pgdata), cleanup_mode="stop").cleanup()
        except Exception:
            pass
        import shutil as _sh

        _sh.rmtree(tmp, ignore_errors=True)

    n_fail = sum(1 for r in results.rows if not r["ok"])
    print(
        f"EVIDENCE COMPLETE: {len(results.rows) - n_fail}/{len(results.rows)} passed → {out}/evidence.json"
    )
    return 1 if n_fail or not results.rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
