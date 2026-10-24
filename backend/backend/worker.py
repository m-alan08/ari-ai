"""Background worker: claims jobs from Postgres and runs registered handlers.

    uv run python -m backend.worker            # run forever
    uv run python -m backend.worker --once     # drain available jobs then exit (tests/CI)

Long LLM/agent work never runs in the API process; it runs here. A heartbeat
thread extends the lease while a handler runs and surfaces cancellation.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend import alerts, jobs
from backend.db import transaction
from backend.settings import get_settings
from shared.errors import ErrorCode

log = logging.getLogger("backend.worker")


class JobCancelled(Exception):
    pass


class PermanentJobError(Exception):
    """Non-retryable failure (bad input, policy denial). Recorded, not retried."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class JobContext:
    job: jobs.Job
    owner: str
    _cancelled: threading.Event = field(default_factory=threading.Event)
    _lease_lost: threading.Event = field(default_factory=threading.Event)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise JobCancelled(self.job.id)
        if self._lease_lost.is_set():
            raise JobCancelled(f"{self.job.id}: lease lost")


Handler = Callable[[JobContext], dict[str, Any] | None]
HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


def _load_handlers() -> None:
    # Importing registers handlers via @handler.
    from backend import delivery, pipeline, recipes, simulated_attack  # noqa: F401
    from backend.live_defense import engine as _live_defense_engine  # noqa: F401
    from backend.live_defense import first_responder as _live_defense_fr  # noqa: F401


def _heartbeat_loop(ctx: JobContext, lease_s: int, stop: threading.Event) -> None:
    interval = max(1.0, lease_s / 3)
    while not stop.wait(interval):
        try:
            with transaction() as conn:
                ok = jobs.heartbeat(conn, ctx.job.id, ctx.owner, lease_s)
            if not ok:
                ctx._cancelled.set()
        except Exception:  # pragma: no cover - transient DB errors; lease covers us
            log.exception("heartbeat failed for %s", ctx.job.id)


# Set while a claimed job runs. SIGTERM waits only for that; an idle worker (polling, or stuck
# in a claim) exits at once, so it cannot hold a deploy until the platform's SIGKILL.
_BUSY = threading.Event()


def run_one(owner: str, kinds: list[str] | None = None) -> str | None:
    """Claim and execute at most one job. Returns the job id or None if queue empty."""
    _load_handlers()
    settings = get_settings()
    with transaction() as conn:
        job = jobs.claim(conn, owner, settings.worker_lease_s, kinds)
    if job is None:
        return None
    _BUSY.set()
    ctx = JobContext(job=job, owner=owner)
    stop = threading.Event()
    hb = threading.Thread(
        target=_heartbeat_loop, args=(ctx, settings.worker_lease_s, stop), daemon=True
    )
    hb.start()
    try:
        fn = HANDLERS.get(job.kind)
        if fn is None:
            raise PermanentJobError(ErrorCode.INTERNAL, f"no handler for job kind {job.kind!r}")
        result = fn(ctx)
        ctx.check_cancelled()
        with transaction() as conn:
            if not jobs.complete(conn, job.id, owner, result):
                raise JobCancelled(f"{job.id}: cancelled or lease lost before complete")
        log.info("job %s (%s) succeeded", job.id, job.kind)
    except JobCancelled as exc:
        with transaction() as conn:
            jobs.fail(
                conn,
                job.id,
                owner,
                {"code": ErrorCode.CANCELLED.value, "message": str(exc)},
                retryable=False,
            )
    except PermanentJobError as exc:
        with transaction() as conn:
            jobs.fail(
                conn, job.id, owner, {"code": exc.code.value, "message": str(exc)}, retryable=False
            )
        log.warning("job %s (%s) failed permanently: %s", job.id, job.kind, exc)
        alerts.send_alert(
            "job_failed_permanent",
            details={"job_id": job.id, "kind": job.kind, "error": str(exc)[:300]},
        )
    except Exception as exc:
        with transaction() as conn:
            status = jobs.fail(
                conn,
                job.id,
                owner,
                {
                    "code": ErrorCode.INTERNAL.value,
                    "message": f"{type(exc).__name__}: {exc}"[:500],
                    "trace": traceback.format_exc()[-4000:],
                },
                retryable=True,
            )
        log.exception("job %s (%s) errored; now %s", job.id, job.kind, status)
        if status == "dead":
            # Attempts exhausted: nobody will retry this job. Page a human.
            alerts.send_alert(
                "job_dead",
                details={
                    "job_id": job.id,
                    "kind": job.kind,
                    "attempts": job.attempts,
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                },
            )
    finally:
        stop.set()
        hb.join(timeout=5)
        _BUSY.clear()
    return job.id


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def drain(max_jobs: int = 100, kinds: list[str] | None = None) -> list[str]:
    done = []
    owner = worker_id()
    for _ in range(max_jobs):
        jid = run_one(owner, kinds)
        if jid is None:
            break
        done.append(jid)
    return done


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    a = p.parse_args(argv)
    if not logging.getLogger().handlers:
        # Direct `python -m backend.worker` invocation; the hackatl-worker entrypoint
        # already installed the configured (JSON in staging/prod) handlers.
        from backend.ops_logging import configure_logging

        configure_logging()
    if a.once:
        print(f"processed {len(drain())} jobs")
        return 0
    owner = worker_id()
    log.info("worker %s started", owner)
    # On SIGTERM (a deploy) finish the job in hand, then exit: killing it mid-run would lose
    # the agents' in-memory state and fail the assessment.
    stopping = threading.Event()

    def on_term(*_: object) -> None:
        stopping.set()
        if not _BUSY.is_set():
            log.info("worker %s stopped while idle", owner)
            logging.shutdown()
            os._exit(0)  # a claim interrupted here rolls back with the connection

    signal.signal(signal.SIGTERM, on_term)
    while not stopping.is_set():
        if run_one(owner) is None:
            stopping.wait(get_settings().worker_poll_interval_s)
    log.info("worker %s stopped after finishing its job", owner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
