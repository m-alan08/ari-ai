"""FastAPI application factory: ``uvicorn backend.main:app``."""

from __future__ import annotations

import logging

import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend import jobs
from backend.api import (
    auth,
    github_repos,
    incidents,
    intelligence,
    live_defense,
    live_defense_controls,
    live_defense_first_responder,
    routes,
    runtime,
    webhooks,
)
from backend.api.deps import ApiError
from backend.db import get_engine
from backend.llm import llm_mode
from backend.settings import get_settings
from shared import SCHEMA_VERSION
from shared.api import HealthStatus, ReadinessStatus
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
        return HealthStatus(status="ok", version=VERSION)

    @app.get("/readyz", response_model=ReadinessStatus, tags=["ops"])
    def readyz() -> JSONResponse:
        db_ok = queue_ok = False
        try:
            with get_engine().begin() as conn:
                conn.execute(sa.text("SELECT 1"))
                db_ok = True
                queue_ok = jobs.queue_reachable(conn)
        except Exception:
            log.exception("readiness check failed")
        status = ReadinessStatus(
            status="ready" if db_ok and queue_ok else "not_ready",
            database=db_ok,
            queue=queue_ok,
            llm_mode=llm_mode(),
            github_app_configured=bool(
                s.github_app_id and (s.github_app_private_key_path or s.github_app_private_key)
            ),
            webhook_secret_configured=bool(
                s.github_webhook_secret
                and s.github_webhook_secret.get_secret_value() not in ("", "replace-me")
            ),
        )
        return JSONResponse(
            status.model_dump(mode="json"), status_code=200 if db_ok and queue_ok else 503
        )

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
    ):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
