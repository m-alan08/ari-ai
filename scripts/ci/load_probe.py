#!/usr/bin/env python3
"""Bounded load + recovery probe for the durable job queue (real PostgreSQL).

Creates a scratch database on the CI/embedded PostgreSQL, migrates it, then:
  1. enqueues ``workers * 25`` no-op jobs and drains them with ``workers``
     concurrent claimers — asserts exactly-once (every job completes once,
     attempts == 1) and reports enqueue/drain rates;
  2. crash recovery: a job claimed and abandoned completes after lease expiry
     exactly once more;
  3. dedupe under concurrency: two racing enqueues of one dedupe_key create
     exactly one job;
  4. dead-letter: a retryable job past max_attempts lands in ``dead`` and is
     visible in a status query (dead-letter visibility).

Env: TEST_DATABASE_URL (admin URL of the database service), LOAD_WORKERS.
Exits non-zero on any failed invariant. Prints a JSON report for the summary.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from collections import Counter

import sqlalchemy as sa


def _scratch_url() -> tuple[str, str]:
    admin_url = os.environ["TEST_DATABASE_URL"]
    if admin_url.startswith("postgresql://"):
        admin_url = "postgresql+psycopg://" + admin_url[len("postgresql://") :]
    db = "load_probe_" + uuid.uuid4().hex[:12]
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{db}"'))
    admin.dispose()
    return admin_url, sa.engine.make_url(admin_url).set(database=db).render_as_string(
        hide_password=False
    )


def main() -> int:
    workers = int(os.environ.get("LOAD_WORKERS", "4"))
    n_jobs = workers * 25
    report: dict = {"workers": workers, "jobs": n_jobs, "checks": {}}

    admin_url, db_url = _scratch_url()
    db_name = sa.engine.make_url(db_url).database
    os.environ["DATABASE_URL"] = db_url

    from backend import jobs
    from backend.db import get_engine, transaction
    from backend.db import jobs as jobs_t
    from backend.migrate import upgrade

    engine = get_engine()
    try:
        upgrade("heads")
        t0 = time.monotonic()

        # --- 1. bounded concurrent drain, exactly-once -------------------------
        with transaction() as conn:
            for i in range(n_jobs):
                jobs.enqueue(conn, "probe_noop", {"i": i}, dedupe_key=f"probe-{i}")
        enq_s = time.monotonic() - t0

        completed: list[str] = []
        done_at = time.monotonic() + 90
        lock = threading.Lock()

        def drainer(w: int) -> None:
            while time.monotonic() < done_at:
                with transaction() as conn:
                    job = jobs.claim(conn, f"probe-worker-{w}", lease_s=10)
                    if job is not None:
                        with lock:
                            completed.append(job.id)
                        jobs.complete(conn, job.id, f"probe-worker-{w}", {"ok": True})
                        continue
                # no claimable job: either the queue is drained or backoff — recheck
                with transaction() as conn:
                    remaining = conn.execute(
                        sa.select(sa.func.count())
                        .select_from(jobs_t)
                        .where(jobs_t.c.status.in_(("queued", "running")))
                    ).scalar_one()
                if remaining == 0:
                    return
                time.sleep(0.05)

        threads = [threading.Thread(target=drainer, args=(w,)) for w in range(workers)]
        t1 = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        drain_s = time.monotonic() - t1

        counts = Counter(completed)
        dup = [j for j, n in counts.items() if n > 1]
        report["checks"]["exactly_once"] = not dup and len(counts) == n_jobs
        report["duplicates"] = dup[:5]
        report["enqueue_s"] = round(enq_s, 2)
        report["drain_s"] = round(drain_s, 2)
        report["jobs_per_s"] = round(n_jobs / max(drain_s, 0.001), 1)
        with transaction() as conn:
            attempts = dict(
                conn.execute(
                    sa.select(jobs_t.c.id, jobs_t.c.attempts).where(
                        jobs_t.c.dedupe_key.like("probe-%")
                    )
                ).all()
            )
        report["checks"]["attempts_eq_1"] = all(a == 1 for a in attempts.values())

        # --- 2. crash recovery: abandoned lease re-claimed exactly once --------
        with transaction() as conn:
            jid, _ = jobs.enqueue(conn, "probe_crash", {}, dedupe_key="probe-crash")
        with transaction() as conn:
            job = jobs.claim(conn, "crashed-worker", lease_s=1)
            assert job and job.id == jid
        time.sleep(2.5)  # let the lease expire
        with transaction() as conn:
            job2 = jobs.claim(conn, "recovery-worker", lease_s=10)
            assert job2 and job2.id == jid and job2.attempts == 2, job2
            jobs.complete(conn, job2.id, "recovery-worker")
        report["checks"]["crash_recovery"] = True

        # --- 3. dedupe under concurrency ----------------------------------------
        results: list[bool] = []

        def racy_enqueue() -> None:
            with transaction() as conn:
                _, created = jobs.enqueue(conn, "probe_dup", {}, dedupe_key="probe-dup")
            results.append(created)

        t1, t2 = threading.Thread(target=racy_enqueue), threading.Thread(target=racy_enqueue)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        report["checks"]["dedupe_race_one_created"] = results.count(True) == 1
        # drain the surviving dedupe job so it cannot pollute the next assertion
        with transaction() as conn:
            leftover = jobs.claim(conn, "cleanup-worker", lease_s=10)
            assert leftover is not None
            jobs.complete(conn, leftover.id, "cleanup-worker")

        # --- 4. dead-letter visibility -------------------------------------------
        with transaction() as conn:
            jid, _ = jobs.enqueue(conn, "probe_dead", {}, dedupe_key="probe-dead", max_attempts=1)
            job = jobs.claim(conn, "w1", lease_s=10)
            jobs.fail(conn, job.id, "w1", {"code": "X", "message": "boom"}, retryable=True)
        with transaction() as conn:
            again = jobs.claim(conn, "w2", lease_s=10)
            assert again is None or again.id != jid  # attempts exhausted -> dead, not re-claimed
            status = conn.execute(sa.select(jobs_t.c.status).where(jobs_t.c.id == jid)).scalar_one()
            visible = conn.execute(
                sa.select(sa.func.count())
                .select_from(jobs_t)
                .where(jobs_t.c.status.in_(("dead", "failed")))
            ).scalar_one()
        report["checks"]["dead_letter"] = status == "dead"
        report["checks"]["dead_letter_visible"] = visible >= 1

        report["ok"] = all(report["checks"].values())
    finally:
        engine.dispose()
        admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        admin.dispose()

    print(json.dumps(report, indent=2))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(
                "### Load/recovery probe\n\n```json\n" + json.dumps(report, indent=2) + "\n```\n"
            )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
