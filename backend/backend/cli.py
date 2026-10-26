"""Console entry points: hackatl-api, hackatl-worker, hackatl-migrate, hackatl-preflight."""

from __future__ import annotations


def serve() -> None:
    from backend import preflight
    from backend.ops_logging import configure_logging

    configure_logging()
    preflight.require_config()

    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, proxy_headers=True, log_config=None)


def worker() -> None:
    from backend import preflight
    from backend.ops_logging import configure_logging

    configure_logging()
    preflight.require_config(worker=True)
    # A worker on a stale schema fails jobs in ways the queue retries forever;
    # refuse to start instead. `hackatl-migrate` runs first in every deploy path.
    preflight.require_migrated()

    from backend.worker import main

    raise SystemExit(main())


def migrate() -> None:
    from backend.migrate import upgrade

    upgrade()
    print("database migrated to all current heads")


def preflight() -> None:
    from backend import preflight as pf

    raise SystemExit(pf.run())
