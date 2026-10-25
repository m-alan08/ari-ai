"""Release-readiness probe worker: the real worker loop plus release_probe_* job kinds.

    uv run python deploy/readiness_worker.py [--once]

`deploy/release_readiness.py` spawns this as a subprocess so job-claim, lease,
heartbeat and crash-recovery behavior is exercised through the production code
path — including kill -9 mid-job. It registers three probe kinds before handing
control to `worker.main`, and `worker.run_one` still loads every real handler,
so live jobs (e.g. `live_defense_emergency_stop`) are processed too.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from backend import worker
from backend.worker import JobContext, PermanentJobError, handler
from shared.errors import ErrorCode


@handler("release_probe_success")
def _probe_success(ctx: JobContext) -> dict[str, Any]:
    ctx.check_cancelled()
    return {"probe": "ok", "job_id": ctx.job.id, "owner": ctx.owner}


@handler("release_probe_fail")
def _probe_fail(ctx: JobContext) -> dict[str, Any]:
    # payload.secret lets the readiness run prove redaction of worker logs/alerts.
    secret = ctx.job.payload.get("secret", "")
    raise PermanentJobError(ErrorCode.INTERNAL, f"probe failure {secret}".strip())


@handler("release_probe_sleep")
def _probe_sleep(ctx: JobContext) -> dict[str, Any]:
    """Hold the lease without cancel checks so a mid-job kill is observable."""
    time.sleep(float(ctx.job.payload.get("seconds", 30)))
    return {"slept": True}


def main() -> int:
    from backend import preflight
    from backend.ops_logging import configure_logging

    configure_logging()
    preflight.require_config(worker=True)
    preflight.require_migrated()
    return worker.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
