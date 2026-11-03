"""POST /api/v1/webhooks/github — verified, deduplicated, persisted, enqueued, fast.

Order of checks: size bound -> HMAC over the exact raw body -> required headers ->
JSON parse -> delivery-id dedupe -> redacted payload stored as artifact -> job.
No session auth (GitHub calls this); the HMAC secret is the authentication.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

import sqlalchemy as sa
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend import jobs
from backend.api.deps import ApiError
from backend.artifacts import ArtifactStore
from backend.db import jobs as jobs_table
from backend.db import transaction, webhook_deliveries
from backend.github import verify_signature
from backend.settings import get_settings
from shared.api import WebhookAck
from shared.errors import ErrorCode
from shared.evidence import ArtifactKind

router = APIRouter(tags=["webhooks"])
_DELIVERY = re.compile(r"^[A-Za-z0-9\-]{8,100}$")
_EVENT = re.compile(r"^[a-z_]{2,60}$")
# A redelivery of a delivery whose processing never succeeded must run again; otherwise
# a dead-lettered push could never be recovered through GitHub's "Redeliver" button.
_REPROCESS = ("dead", "failed", "timed_out", "cancelled")
log = logging.getLogger(__name__)


async def _read_bounded(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ApiError(ErrorCode.PAYLOAD_TOO_LARGE, f"payload exceeds {limit} bytes", 413)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise ApiError(ErrorCode.PAYLOAD_TOO_LARGE, f"payload exceeds {limit} bytes", 413)
    return bytes(body)


@router.post("/webhooks/github", status_code=202, response_model=WebhookAck)
async def github_webhook(request: Request) -> JSONResponse:
    s = get_settings()
    secret = s.github_webhook_secret.get_secret_value().encode() if s.github_webhook_secret else b""
    if not secret or secret == b"replace-me":
        raise ApiError(ErrorCode.SUBSYSTEM_UNAVAILABLE, "webhook secret not configured", 503)
    raw = await _read_bounded(request, s.webhook_max_bytes)
    if not verify_signature(secret, raw, request.headers.get("X-Hub-Signature-256")):
        raise ApiError(
            ErrorCode.WEBHOOK_SIGNATURE_INVALID, "invalid or missing X-Hub-Signature-256", 401
        )
    delivery = request.headers.get("X-GitHub-Delivery", "")
    event = request.headers.get("X-GitHub-Event", "")
    if not _DELIVERY.match(delivery) or not _EVENT.match(event):
        raise ApiError(
            ErrorCode.VALIDATION_FAILED, "missing/invalid X-GitHub-Delivery or X-GitHub-Event", 400
        )
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
    except ValueError as e:
        raise ApiError(ErrorCode.VALIDATION_FAILED, f"invalid JSON payload: {e}"[:200], 400) from e
    # No rate limit: the HMAC already proves GitHub sent it, the queue absorbs bursts, and
    # GitHub never retries a 429, so limiting here would silently lose commits.

    def _persist() -> WebhookAck:
        with transaction() as conn:
            existing = conn.execute(
                sa.select(webhook_deliveries.c.job_id)
                .where(webhook_deliveries.c.delivery_id == delivery)
                .with_for_update()
            ).first()
            if existing is not None:
                status = conn.execute(
                    sa.select(jobs_table.c.status).where(jobs_table.c.id == existing[0])
                ).scalar_one_or_none()
                if status in _REPROCESS:
                    job_id, _ = jobs.enqueue(
                        conn,
                        "process_github_delivery",
                        {"delivery_id": delivery},
                        dedupe_key=f"gh:{delivery}:after:{existing[0]}",
                        priority=50,
                    )
                    conn.execute(
                        sa.update(webhook_deliveries)
                        .where(webhook_deliveries.c.delivery_id == delivery)
                        .values(job_id=job_id)
                    )
                    log.warning(
                        "webhook redelivery %s reprocessed: previous job %s was %s",
                        delivery,
                        existing[0],
                        status,
                    )
                    return WebhookAck(
                        delivery_id=delivery, duplicate=False, accepted=True, job_id=job_id
                    )
                return WebhookAck(
                    delivery_id=delivery,
                    duplicate=True,
                    accepted=False,
                    job_id=existing[0],
                    reason="duplicate delivery",
                )
            ref = ArtifactStore().put_json(
                conn, payload, kind=ArtifactKind.webhook_payload, project_id=None
            )
            inserted = conn.execute(
                sa.dialects.postgresql.insert(webhook_deliveries)
                .values(
                    delivery_id=delivery,
                    source="github",
                    event_name=event,
                    payload_artifact_id=ref.id,
                    payload_sha256=hashlib.sha256(raw).hexdigest(),
                )
                .on_conflict_do_nothing()
                .returning(webhook_deliveries.c.delivery_id)
            ).scalar()
            if inserted is None:  # a concurrent identical delivery won the race
                winner = conn.execute(
                    sa.select(webhook_deliveries.c.job_id).where(
                        webhook_deliveries.c.delivery_id == delivery
                    )
                ).scalar_one_or_none()
                return WebhookAck(
                    delivery_id=delivery,
                    duplicate=True,
                    accepted=False,
                    job_id=winner,
                    reason="duplicate delivery",
                )
            job_id, _ = jobs.enqueue(
                conn,
                "process_github_delivery",
                {"delivery_id": delivery},
                dedupe_key=f"gh:{delivery}",
                priority=50,
            )
            conn.execute(
                sa.update(webhook_deliveries)
                .where(webhook_deliveries.c.delivery_id == delivery)
                .values(job_id=job_id)
            )
            return WebhookAck(delivery_id=delivery, duplicate=False, accepted=True, job_id=job_id)

    from fastapi.concurrency import run_in_threadpool

    ack = await run_in_threadpool(_persist)
    return JSONResponse(ack.model_dump(mode="json"), status_code=200 if ack.duplicate else 202)
