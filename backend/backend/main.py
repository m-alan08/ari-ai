"""FastAPI application factory: ``uvicorn backend.main:app``."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend import jobs, ops
from backend.api import (
    auth,
    execution,
    github_repos,
    incidents,
    intelligence,
    live_defense,
    live_defense_controls,
    live_defense_first_responder,
    live_defense_investigations,
    onboarding,
    routes,
    runtime,
    webhooks,
)
from backend.api import (
    ops as ops_api,
)
from backend.api.deps import ApiError, require_user
from backend.settings import get_settings
from shared import SCHEMA_VERSION
from shared.api import DiagnosticsReport, HealthStatus, ReadinessStatus
from shared.errors import ErrorBody, ErrorCode, ErrorResponse
from shared.ids import IdPrefix, new_id

log = logging.getLogger("backend.api")
VERSION = "0.1.0"


def _error(
    request: Request,
    status: int,
    code: ErrorCode,
    message: str,
    details: dict | None = None,
    headers: dict | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message[:500],
            details=details or {},
            request_id=getattr(request.state, "request_id", None),
        )
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status, headers=headers)


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="HackATL agentic security platform",
        version=VERSION,
        description=f"Control-plane API. Contract schema {SCHEMA_VERSION}.",
        docs_url="/api/docs" if s.is_dev else None,
        openapi_url="/api/openapi.json" if s.is_dev else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[s.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "Last-Event-ID"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = new_id(IdPrefix.req)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        headers = (
            {"Retry-After": str(exc.details["retry_after_s"])}
            if "retry_after_s" in exc.details
            else None
        )
        return _error(request, exc.status, exc.code, str(exc), exc.details, headers)

    @app.exception_handler(jobs.QueueBackpressure)
    async def _backpressure(request: Request, exc: jobs.QueueBackpressure) -> JSONResponse:
        return _error(
            request,
            429,
            exc.code,
            str(exc),
            {"retry_after_s": exc.retry_after_s, "pending": exc.pending, "limit": exc.limit},
            {"Retry-After": str(exc.retry_after_s)},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{k: v for k, v in e.items() if k in ("loc", "msg", "type")} for e in exc.errors()]
        return _error(
            request,
            422,
            ErrorCode.VALIDATION_FAILED,
            "request validation failed",
            {"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: ErrorCode.NOT_FOUND, 405: ErrorCode.VALIDATION_FAILED}.get(
            exc.status_code, ErrorCode.INTERNAL
        )
        return _error(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _error(request, 500, ErrorCode.INTERNAL, "internal error")

    @app.get("/healthz", response_model=HealthStatus, tags=["ops"])
    def healthz() -> HealthStatus:
        return HealthStatus(
            status="ok",
            version=VERSION,
            git_sha=s.deployed_git_sha,
            schema_version=SCHEMA_VERSION,
        )

    @app.get("/readyz", response_model=ReadinessStatus, tags=["ops"])
    def readyz() -> JSONResponse:
        """Infra gate (db+queue+worker liveness where required) as HTTP 200/503, plus a
        capabilities block that names each thing an assessment needs separately:
        live-model config, GitHub App credentials, sandbox mode, artifact store.
        "ready" with capabilities_ready=false means the service is up but a real
        assessment cannot run — a config error, visible here not just in logs."""
        body, code = ops.readiness(get_settings())
        return JSONResponse(
            ReadinessStatus.model_validate(body).model_dump(mode="json"), status_code=code
        )

    @app.get("/diagnostics", response_model=DiagnosticsReport, tags=["ops"])
    def diagnostics(request: Request) -> DiagnosticsReport:
        """/readyz plus live verification: GitHub App JWT check, sandbox SDK import,
        migration head vs code, queue counts, worker rows. Owner-only: worker ids,
        env-var names and migration state are internal infrastructure metadata."""
        import sqlalchemy as sa

        from backend.db import get_engine, memberships

        with get_engine().begin() as conn:
            user = require_user(conn, request)
            owner = conn.execute(
                sa.select(memberships.c.user_id)
                .where(memberships.c.user_id == user.id)
                .where(memberships.c.role == "owner")
                .limit(1)
            ).first()
        if owner is None:
            raise ApiError(
                ErrorCode.FORBIDDEN,
                "diagnostics requires an org owner",
                403,
            )
        return DiagnosticsReport.model_validate(ops.diagnostics(get_settings()))

    for r in (
        auth.router,
        github_repos.router,
        routes.router,
        webhooks.router,
        runtime.router,
        intelligence.router,
        incidents.router,
        live_defense.router,
        live_defense_controls.router,
        live_defense_first_responder.router,
        execution.router,
        live_defense_investigations.router,
        ops_api.router,
        onboarding.router,
    ):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
