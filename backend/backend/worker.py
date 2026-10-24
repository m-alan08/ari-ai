"""Background worker: claims jobs from Postgres and runs registered handlers.

    uv run python -m backend.worker            # run forever
    uv run python -m backend.worker --once     # drain available jobs then exit (tests/CI)

Long LLM/agent work never runs in the API process; it runs here. A heartbeat
thread extends the lease while a handler runs and surfaces cancellation and
deadline exhaustion. Workers register in ``worker_heartbeats`` and periodically run
``backend.execution.reconcile`` (docs/architecture/execution-reliability.md).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import exc as sa_exc

from backend import execution, jobs
from backend.db import transaction
from backend.jobs import ErrorCategory
from backend.settings import get_settings
from shared.base import utcnow
from shared.errors import ErrorCode

log = logging.getLogger("backend.worker")


class JobCancelled(Exception):
    pass


class JobDeferred(Exception):
    """Handler is waiting on a prerequisite. Do not consume a failure attempt."""

    def __init__(self, delay_s: float = 2.0, message: str = "prerequisite not ready") -> None:
        super().__init__(message)
        self.delay_s = delay_s


class JobDeadlineExceeded(JobCancelled):
    """The job's execution deadline passed; it ends ``timed_out``, not retried."""


class EffectNotApplied(Exception):
    """Raise inside ``JobContext.effect`` when the effect definitely did not happen
    (e.g. the remote rejected the request before acting). The job stays retryable."""



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
    _deadline: threading.Event = field(default_factory=threading.Event)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check_cancelled(self) -> None:
        if self._deadline.is_set() or (
            self.job.deadline_at is not None and utcnow() >= self.job.deadline_at
        ):
            raise JobDeadlineExceeded(f"{self.job.id}: execution deadline exceeded")
        if self._cancelled.is_set():
            raise JobCancelled(self.job.id)
        if self._lease_lost.is_set():
            raise JobCancelled(f"{self.job.id}: lease lost")

    @contextmanager
    def effect(self, key: str) -> Iterator[None]:
        """Guard one external side effect (adapter call, remote write).

        The intent is committed before the body runs. If the worker then dies or the
        body raises, the job becomes ``recovery_required`` instead of being re-run, so
        an effect with an uncertain outcome is never replayed automatically. Raise
        ``EffectNotApplied`` from the body when the effect definitely did not happen.
        """
        self.check_cancelled()
        with transaction() as conn:
            if not jobs.begin_effect(conn, self.job.id, self.owner, key):
                raise JobCancelled(f"{self.job.id}: lease, cancellation or deadline blocks effect")
        try:
            yield
        except EffectNotApplied:
            with transaction() as conn:
                jobs.end_effect(conn, self.job.id, self.owner, applied=False)
            raise
        with transaction() as conn:
            jobs.end_effect(conn, self.job.id, self.owner, applied=True)


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
                state = jobs.heartbeat_state(conn, ctx.job.id, ctx.owner, lease_s)
                execution.worker_seen(conn, ctx.owner, "busy", ctx.job.id)
            if state == "lost":
                ctx._lease_lost.set()
                ctx._cancelled.set()
            elif state == "cancelled":
                ctx._cancelled.set()
            elif state == "deadline":
                ctx._deadline.set()
        except Exception:  # pragma: no cover - transient DB errors; lease covers us
            log.exception("heartbeat failed for %s", ctx.job.id)


# Set while a claimed job runs. SIGTERM waits only for that; an idle worker (polling, or stuck
# in a claim) exits at once, so it cannot hold a deploy until the platform's SIGKILL.
_BUSY = threading.Event()

_AUTHZ_CODES = frozenset(
    {
        ErrorCode.FORBIDDEN,
        ErrorCode.SCOPE_DENIED,
        ErrorCode.APPROVAL_REQUIRED,
        ErrorCode.LIVE_CONTROL_DENIED,
        ErrorCode.KILL_SWITCH,
    }
)


def classify(exc: BaseException) -> tuple[ErrorCategory, bool]:
    """Structured failure category and whether a retry may help."""
    if isinstance(exc, JobDeadlineExceeded):
        return ErrorCategory.deadline_exceeded, False
    if isinstance(exc, JobCancelled):
        return ErrorCategory.cancelled, False
    if isinstance(exc, PermanentJobError):
        if exc.code in _AUTHZ_CODES:
            return ErrorCategory.authorization, False
        return ErrorCategory.permanent, False
    if isinstance(exc, jobs.QueueBackpressure):
        return ErrorCategory.backpressure, True
    if isinstance(exc, (sa_exc.OperationalError, sa_exc.InterfaceError, sa_exc.DisconnectionError)):
        return ErrorCategory.infrastructure, True
    if isinstance(exc, sa_exc.DBAPIError) and exc.connection_invalidated:
        return ErrorCategory.infrastructure, True
    if isinstance(exc, (httpx.TransportError, ConnectionError, TimeoutError)):
        return ErrorCategory.network, True
    return ErrorCategory.transient, True


def _db_retry[T](fn: Callable[[], T], what: str) -> T | None:
    """Run a finalizing transaction, retrying through brief database outages. If it
    still fails, the lease expires and reconciliation recovers the job."""
    attempts = max(1, get_settings().worker_finalize_retries)
    for i in range(attempts):
        try:
            return fn()
        except (sa_exc.OperationalError, sa_exc.InterfaceError, sa_exc.DisconnectionError):
            if i == attempts - 1:
                log.exception("%s failed after %d attempts; lease expiry will recover", what, i + 1)
                return None
            time.sleep(min(5.0, 0.2 * 2**i))
    return None  # pragma: no cover


def _finalize_fail(job: jobs.Job, owner: str, error: dict[str, Any], exc: BaseException) -> str:
    category, retryable = classify(exc)

    def go() -> str:
        with transaction() as conn:
            return jobs.fail(conn, job.id, owner, error, retryable=retryable, category=category)

    return _db_retry(go, f"recording failure of {job.id}") or "unrecorded"


def _registry(owner: str, state: Any, job_id: str | None = None, finished: int = 0) -> None:
    try:
        with transaction() as conn:
            execution.worker_seen(conn, owner, state, job_id, finished=finished)
    except Exception:  # registry is observability only; never block execution on it
        log.warning("worker registry update failed for %s", owner, exc_info=True)


def run_one(owner: str, kinds: list[str] | None = None) -> str | None:
    """Claim and execute at most one job. Returns the job id or None if queue empty."""
    _load_handlers()
    settings = get_settings()
    with transaction() as conn:
        job = jobs.claim(conn, owner, settings.worker_lease_s, kinds)
    if job is None:
        return None
    _BUSY.set()
    _registry(owner, "busy", job.id)
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

        def done() -> bool:
            with transaction() as conn:
                return jobs.complete(conn, job.id, owner, result)

        completed = _db_retry(done, f"completing {job.id}")
        if completed is False:
            raise JobCancelled(f"{job.id}: cancelled or lease lost before complete")
        if completed:
            log.info("job %s (%s) succeeded", job.id, job.kind)
    except JobDeferred as exc:
        with transaction() as conn:
            jobs.defer(conn, job.id, owner, delay_s=exc.delay_s)
        log.info("job %s (%s) deferred: %s", job.id, job.kind, exc)
    except JobCancelled as exc:
        code = (
            ErrorCode.JOB_LEASE_EXPIRED
            if isinstance(exc, JobDeadlineExceeded)
            else ErrorCode.CANCELLED
        )
        _finalize_fail(job, owner, {"code": code.value, "message": str(exc)}, exc)
    except PermanentJobError as exc:
        _finalize_fail(job, owner, {"code": exc.code.value, "message": str(exc)}, exc)
        log.warning("job %s (%s) failed permanently: %s", job.id, job.kind, exc)
    except Exception as exc:
        status = _finalize_fail(
            job,
            owner,
            {
                "code": ErrorCode.INTERNAL.value,
                "message": f"{type(exc).__name__}: {exc}"[:500],
                "trace": traceback.format_exc()[-4000:],
            },
            exc,
        )
        log.exception("job %s (%s) errored; now %s", job.id, job.kind, status)
    finally:
        stop.set()
        hb.join(timeout=5)
        _registry(owner, "idle", None, finished=1)
        _BUSY.clear()
    return job.id


def reconcile_safely() -> dict[str, Any] | None:
    try:
        with transaction() as conn:
            return execution.reconcile(conn)
    except Exception:
        log.exception("reconcile failed; will retry next interval")
        return None


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def drain(max_jobs: int = 100, kinds: list[str] | None = None) -> list[str]:
    done = []
    owner = worker_id()
    reconcile_safely()
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
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
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
    # Startup reconciliation recovers jobs orphaned by a crash or restart of the fleet.
    reconcile_safely()
    last_reconcile = last_seen = time.monotonic()
    _registry(owner, "idle")
    while not stopping.is_set():
        s = get_settings()
        now = time.monotonic()
        if now - last_reconcile >= s.worker_reconcile_interval_s:
            reconcile_safely()
            last_reconcile = now
        if now - last_seen >= max(1.0, s.worker_stale_s / 4):
            _registry(owner, "idle")
            last_seen = now
        if run_one(owner) is None:
            stopping.wait(s.worker_poll_interval_s)
    try:
        with transaction() as conn:
            execution.worker_gone(conn, owner)
    except Exception:  # pragma: no cover - the stale row ages out of the active count
        log.warning("could not deregister worker %s", owner, exc_info=True)
    log.info("worker %s stopped after finishing its job", owner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
