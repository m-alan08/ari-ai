"""hooks-lite receiver: accepts signed webhooks, issues API sessions, reads jobs.

Intended behavior:

- Every webhook delivery must carry a valid HMAC-SHA256 signature.
- Deliveries older than the tolerance window are replays and are rejected.
- Session cookies are Secure, HttpOnly and SameSite=Lax.
- Job status reads require an API session.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from pydantic import BaseModel

from store import ApiSession, Event, Job, Store, seeded_store
from worker import delivery_is_fresh, verify_signature

WEBHOOK_SECRET = "fixture-webhook-secret"  # synthetic fixture value, not a real secret
SESSION_MAX_AGE_S = 3600


class LoginIn(BaseModel):
    principal: str
    password: str


def create_app(store: Store | None = None) -> FastAPI:
    app = FastAPI(title="hooks-lite", version="0.1.0")
    app.state.store = store or seeded_store()

    def get_store() -> Store:
        return app.state.store

    def current_session(
        authorization: Annotated[str | None, Header()] = None,
        store: Store = Depends(get_store),
    ) -> ApiSession:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        sess = store.sessions.get(authorization.removeprefix("Bearer "))
        if sess is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
        return sess

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/github", status_code=status.HTTP_202_ACCEPTED)
    async def receive_github(
        request: Request,
        x_signature_256: Annotated[str | None, Header()] = None,
        x_delivery_id: Annotated[str | None, Header()] = None,
        x_delivery_epoch: Annotated[str | None, Header()] = None,
        store: Store = Depends(get_store),
    ) -> dict[str, str]:
        """Accept a signed, fresh webhook delivery and queue it as an event."""
        body = await request.body()
        if not verify_signature(WEBHOOK_SECRET, body, x_signature_256):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad signature")
        if x_delivery_id is None or x_delivery_id in store.seen_deliveries:
            raise HTTPException(409, "duplicate delivery")
        if x_delivery_epoch is None or not delivery_is_fresh(float(x_delivery_epoch)):
            raise HTTPException(400, "stale delivery")
        store.seen_deliveries[x_delivery_id] = time.time()
        event_id = store.next_id("evt")
        store.events[event_id] = Event(event_id, "github", x_delivery_id, {"bytes": len(body)})
        return {"event_id": event_id}

    @app.post("/sessions/login")
    def login(body: LoginIn, response: Response, store: Store = Depends(get_store)) -> dict[str, str]:
        """Issue an API session cookie for a known fixture principal."""
        if body.principal != "svc_ops" or body.password != "fixture-password":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad credentials")
        token = f"sess_{store.next_id('t')}"
        store.sessions[token] = ApiSession(token, body.principal)
        response.set_cookie(
            "hooks_session",
            token,
            max_age=SESSION_MAX_AGE_S,
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return {"token": token}

    @app.get("/jobs/{job_id}")
    def get_job(
        job_id: str,
        session: ApiSession = Depends(current_session),
        store: Store = Depends(get_store),
    ) -> dict[str, str | None]:
        job = store.jobs.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        return {"id": job.id, "kind": job.kind, "status": job.status, "artifact_url": job.artifact_url}

    return app


app = create_app()
