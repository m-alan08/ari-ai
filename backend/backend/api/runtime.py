"""Runtime signal ingestion (docs/10 "Runtime anomaly signals", docs/13 /ingest/runtime).

* Owners mint per-project ingest tokens (shown once, stored as SHA-256) and revoke them.
* ``POST /ingest/runtime/{project_id}`` accepts aggregated per-route counters only (no
  raw logs or payloads), authenticated by ``Authorization: Bearer <ingest token>``,
  size-bounded and rate-limited. Each window is scored against that route's own
  history with ``monitoring.runtime.score_window``; replays of a window are idempotent.
* Scores at or above the threshold are recorded as ``log_anomaly`` MonitoringEvents,
  labeled with their source and linked to open findings on the same route. They never
  start assessments or widen scope; they feed the monitoring feed and RL facts.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend import audit, jobs
from backend.api.deps import ApiError, limiter, not_found, require_project, require_user
from backend.api.webhooks import _read_bounded
from backend.db import (
    connected_applications,
    findings,
    ingest_tokens,
    monitoring_events,
    repositories,
    runtime_windows,
    transaction,
)
from backend.live_defense.quarantine import secret_like_reason
from backend.runtime_keys import runtime_event_key
from backend.services import project_org
from monitoring.api import ANOMALY_EVENT_THRESHOLD, score_window
from shared.audit import ActorType
from shared.base import utcnow
from shared.errors import ErrorCode
from shared.findings import Finding, FindingKind, FindingStatus
from shared.ids import IdPrefix, new_id
from shared.monitoring import (
    AUTHENTICATED_RUNTIME_SOURCES,
    EventSource,
    EventStatus,
    EventType,
    IngestTokenCreated,
    MonitoringEvent,
    RouteScore,
    RouteWindow,
    RuntimeIngestAck,
    RuntimeSignalBatch,
)
from shared.tenancy import MemberRole

router = APIRouter(tags=["runtime"])
HISTORY_WINDOWS = 12
MAX_BODY = 256 * 1024
OPEN = {
    s.value
    for s in (
        FindingStatus.hypothesis,
        FindingStatus.validated,
        FindingStatus.diagnosed,
        FindingStatus.patch_proposed,
        FindingStatus.patch_rejected,
        FindingStatus.unresolved,
        FindingStatus.needs_recheck,
    )
}


@router.post("/projects/{project_id}/attack-drills", status_code=202)
def start_attack_drill(project_id: str, request: Request) -> dict[str, Any]:
    """Labelled simulated attack: Claude plans it, the real detection pipeline scores it."""
    with transaction() as conn:
        user = require_user(conn, request)
        org_id, _ = require_project(conn, user, project_id, MemberRole.member)
        limiter.check(f"drill:{project_id}", 10, 3600)
        job_id, _ = jobs.enqueue(conn, "attack_drill", {"project_id": project_id}, max_attempts=1)
        audit.record(
            conn,
            org_id=org_id,
            project_id=project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="attack_drill.requested",
            subject_type="job",
            subject_id=job_id,
        )
        return {"job_id": job_id, "simulated": True}


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.post(
    "/projects/{project_id}/ingest-tokens", status_code=201, response_model=IngestTokenCreated
)
def create_ingest_token(project_id: str, request: Request) -> IngestTokenCreated:
    with transaction() as conn:
        user = require_user(conn, request)
        org_id, _ = require_project(conn, user, project_id, MemberRole.owner)
        token = "hkt_ing_" + secrets.token_urlsafe(32)
        tid = new_id(IdPrefix.itk)
        conn.execute(
            sa.insert(ingest_tokens).values(
                id=tid, project_id=project_id, token_sha256=_sha(token), created_by=user.id
            )
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=project_id,
            actor_type=ActorType.user,
            actor_id=user.id,
            action="ingest_token.created",
            subject_type="ingest_token",
            subject_id=tid,
        )
        return IngestTokenCreated(id=tid, project_id=project_id, token=token, created_at=utcnow())


@router.post("/ingest-tokens/{token_id}/revoke", status_code=204)
def revoke_ingest_token(token_id: str, request: Request) -> None:
    with transaction() as conn:
        user = require_user(conn, request)
        row = (
            conn.execute(sa.select(ingest_tokens).where(ingest_tokens.c.id == token_id))
            .mappings()
            .first()
        )
        if row is None:
            raise not_found("ingest token")
        org_id, _ = require_project(conn, user, row["project_id"], MemberRole.owner)
        conn.execute(
            sa.update(ingest_tokens)
            .where(ingest_tokens.c.id == token_id, ingest_tokens.c.revoked_at.is_(None))
            .values(revoked_at=sa.func.now())
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=row["project_id"],
            actor_type=ActorType.user,
            actor_id=user.id,
            action="ingest_token.revoked",
            subject_type="ingest_token",
            subject_id=token_id,
        )


def _authenticate(
    conn: sa.Connection, project_id: str, header: str | None
) -> tuple[str, str | None]:
    if not header or not header.startswith("Bearer "):
        raise ApiError(
            ErrorCode.UNAUTHENTICATED, "ingest token required (Authorization: Bearer ...)", 401
        )
    row = conn.execute(
        sa.select(
            ingest_tokens.c.id,
            ingest_tokens.c.project_id,
            ingest_tokens.c.revoked_at,
            ingest_tokens.c.application_id,
        ).where(ingest_tokens.c.token_sha256 == _sha(header[7:].strip()))
    ).first()
    if (
        row is None
        or row.revoked_at is not None
        or not secrets.compare_digest(row.project_id, project_id)
    ):
        raise ApiError(ErrorCode.UNAUTHENTICATED, "invalid or revoked ingest token", 401)
    if row.application_id:
        app = (
            conn.execute(
                sa.select(connected_applications.c.doc).where(
                    connected_applications.c.id == row.application_id
                )
            )
            .mappings()
            .first()
        )
        if app is not None and (app["doc"] or {}).get("disconnected_at"):
            raise ApiError(ErrorCode.UNAUTHENTICATED, "invalid or revoked ingest token", 401)
    return row.id, row.application_id


def _open_findings_on(conn: sa.Connection, project_id: str, route: str) -> list[str]:
    ids = []
    for doc in conn.execute(
        sa.select(findings.c.doc).where(
            findings.c.project_id == project_id,
            findings.c.kind == FindingKind.security.value,
            findings.c.status.in_(OPEN),
        )
    ).scalars():
        f = Finding.model_validate(doc)
        if route in f.subject_refs:
            ids.append(f.id)
    return ids


def ingest(
    project_id: str,
    token_id: str,
    batch: RuntimeSignalBatch,
    application_id: str | None = None,
    *,
    test_event: bool = False,
) -> RuntimeIngestAck:
    window_s = (batch.window_end - batch.window_start).total_seconds()
    scores: list[RouteScore] = []
    event_ids: list[str] = []
    with transaction() as conn:
        conn.execute(
            sa.update(ingest_tokens)
            .where(ingest_tokens.c.id == token_id)
            .values(last_used_at=sa.func.now())
        )
        if batch.repo_id is not None:
            owner = conn.execute(
                sa.select(repositories.c.project_id).where(repositories.c.id == batch.repo_id)
            ).scalar()
            if owner != project_id:
                raise ApiError(ErrorCode.FORBIDDEN, "repo_id does not belong to this project", 403)
        for window in batch.routes:
            existing = conn.execute(
                sa.select(runtime_windows.c.score).where(
                    runtime_windows.c.project_id == project_id,
                    runtime_windows.c.source == batch.source,
                    runtime_windows.c.route == window.route,
                    runtime_windows.c.window_end == batch.window_end,
                    runtime_windows.c.application_id.is_not_distinct_from(application_id),
                )
            ).scalar()
            if existing is not None:  # replayed window: idempotent, no new event
                scores.append(RouteScore.model_validate(existing))
                continue
            hist_rows = conn.execute(
                sa.select(
                    runtime_windows.c.counters,
                    runtime_windows.c.window_start,
                    runtime_windows.c.window_end,
                )
                .where(
                    runtime_windows.c.project_id == project_id,
                    runtime_windows.c.source == batch.source,
                    runtime_windows.c.route == window.route,
                    runtime_windows.c.window_end < batch.window_end,
                    runtime_windows.c.window_end > batch.window_end - timedelta(hours=24),
                    runtime_windows.c.application_id.is_not_distinct_from(application_id),
                )
                .order_by(runtime_windows.c.window_end.desc())
                .limit(HISTORY_WINDOWS)
            ).all()
            history = [RouteWindow.model_validate(r.counters) for r in hist_rows]
            seconds = [(r.window_end - r.window_start).total_seconds() for r in hist_rows]
            sc = score_window(
                window,
                history,
                window_seconds=window_s,
                history_seconds=seconds,
                source=batch.source,
                window_end=batch.window_end,
            )
            inserted = conn.execute(
                pg_insert(runtime_windows)
                .values(
                    id=new_id(IdPrefix.rtw),
                    project_id=project_id,
                    source=batch.source,
                    route=window.route,
                    window_start=batch.window_start,
                    window_end=batch.window_end,
                    counters=window.model_dump(mode="json"),
                    score=sc.model_dump(mode="json"),
                    application_id=application_id,
                )
                .on_conflict_do_nothing()
                .returning(runtime_windows.c.id)
            ).scalar()
            scores.append(sc)
            if inserted is None or sc.anomaly_score < ANOMALY_EVENT_THRESHOLD:
                continue
            linked = _open_findings_on(conn, project_id, window.route)
            reason = "; ".join(sc.reasons) + (
                f"; open findings on route: {', '.join(linked)}" if linked else ""
            )
            ev = MonitoringEvent(
                id=new_id(IdPrefix.evt),
                project_id=project_id,
                repo_id=batch.repo_id,
                source=EventSource.runtime,
                type=EventType.log_anomaly,
                occurred_at=batch.window_end,
                received_at=utcnow(),
                dedupe_key=runtime_event_key(
                    batch.source, application_id, window.route, batch.window_end
                ),
                simulated=batch.source not in AUTHENTICATED_RUNTIME_SOURCES,
                test_event=test_event,
                status=EventStatus.processed,
                status_reason=f"[{batch.source}] {window.route} anomaly={sc.anomaly_score:.2f}: {reason}"[
                    :500
                ],
            )
            conn.execute(
                pg_insert(monitoring_events)
                .values(
                    id=ev.id,
                    project_id=project_id,
                    repo_id=batch.repo_id,
                    dedupe_key=ev.dedupe_key,
                    status=ev.status.value,
                    doc=ev.model_dump(mode="json"),
                )
                .on_conflict_do_nothing()
            )
            from backend.incidents import correlate_anomaly

            correlate_anomaly(conn, ev, linked, window.route)
            event_ids.append(ev.id)
        if batch.source in AUTHENTICATED_RUNTIME_SOURCES:
            jobs.enqueue(
                conn,
                "live_defense_shadow",
                {
                    "project_id": project_id,
                    "application_id": application_id,
                    "window_end": batch.window_end.isoformat(),
                    "source": batch.source,
                },
                dedupe_key=(
                    f"live_defense_shadow:{project_id}:{application_id or '-'}"
                    f":{batch.window_end.isoformat()}:{batch.source}"
                ),
            )
        audit.record(
            conn,
            org_id=project_org(conn, project_id),
            project_id=project_id,
            actor_type=ActorType.system,
            actor_id=f"ingest:{token_id}",
            action="runtime.ingested",
            subject_type="project",
            subject_id=project_id,
            details={
                "source": batch.source,
                "routes": len(batch.routes),
                "anomalies": len(event_ids),
                "application_id": application_id,
            },
        )
    return RuntimeIngestAck(
        accepted_routes=len(batch.routes),
        anomalies=len(event_ids),
        monitoring_event_ids=event_ids,
        scores=scores,
    )


@router.post("/ingest/runtime/{project_id}", status_code=202, response_model=RuntimeIngestAck)
async def ingest_runtime(project_id: str, request: Request) -> JSONResponse:
    raw = await _read_bounded(request, MAX_BODY)

    def _auth() -> tuple[str, str | None]:
        with transaction() as conn:
            return _authenticate(conn, project_id, request.headers.get("Authorization"))

    token_id, application_id = await run_in_threadpool(_auth)
    limiter.check(f"ingest:{token_id}", 60, 60)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ApiError(ErrorCode.VALIDATION_FAILED, "invalid JSON", 422) from e
    secret_reason = secret_like_reason(parsed)
    if secret_reason:
        await run_in_threadpool(_record_quarantine, project_id, token_id, secret_reason)
        raise ApiError(ErrorCode.VALIDATION_FAILED, secret_reason, 422)
    try:
        batch = RuntimeSignalBatch.model_validate(parsed)
    except ValidationError as e:
        errors: Any = e.errors(include_url=False)
        raise ApiError(
            ErrorCode.VALIDATION_FAILED,
            "invalid runtime signal batch",
            422,
            {"errors": json.loads(json.dumps(errors, default=str))[:20]},
        ) from e
    if batch.source == "owned_staging" and application_id is None:
        raise ApiError(
            ErrorCode.VALIDATION_FAILED,
            "owned_staging ingest requires an application-bound token",
            422,
        )
    ack = await run_in_threadpool(ingest, project_id, token_id, batch, application_id)
    return JSONResponse(ack.model_dump(mode="json"), status_code=202)


def _record_quarantine(project_id: str, token_id: str, reason: str) -> None:
    """Persist that a batch was dropped. Do not store the rejected payload."""
    ev = MonitoringEvent(
        id=new_id(IdPrefix.evt),
        project_id=project_id,
        source=EventSource.runtime,
        type=EventType.log_anomaly,
        occurred_at=utcnow(),
        received_at=utcnow(),
        dedupe_key=f"quarantine:{token_id}:{utcnow().isoformat()}"[:500],
        simulated=False,
        status=EventStatus.ignored,
        status_reason=reason[:500],
    )
    with transaction() as conn:
        conn.execute(
            pg_insert(monitoring_events)
            .values(
                id=ev.id,
                project_id=project_id,
                repo_id=None,
                dedupe_key=ev.dedupe_key,
                status=ev.status.value,
                doc=ev.model_dump(mode="json"),
            )
            .on_conflict_do_nothing()
        )
        audit.record(
            conn,
            org_id=project_org(conn, project_id),
            project_id=project_id,
            actor_type=ActorType.system,
            actor_id=f"ingest:{token_id}",
            action="runtime.quarantined",
            subject_type="project",
            subject_id=project_id,
            details={"reason": reason, "payload_stored": False},
        )


def latest_route_detections(
    conn: sa.Connection, project_id: str, within: timedelta = timedelta(minutes=30)
) -> list[dict[str, Any]]:
    """The most recent window per (source, route) inside ``within`` that carries
    per-principal counters (contracts 0.14): input for RL RouteDetection. Drill windows are
    excluded, as for route signals. Only reporters that detect principals send these."""
    rows = conn.execute(
        sa.select(runtime_windows.c.source, runtime_windows.c.route, runtime_windows.c.counters)
        .where(
            runtime_windows.c.project_id == project_id,
            runtime_windows.c.source != "drill",
            runtime_windows.c.window_end > utcnow() - within,
        )
        .order_by(runtime_windows.c.window_end.desc())
    ).all()
    seen: set[tuple[str, str]] = set()
    out = []
    for row in rows:
        key = (row.source, row.route)
        if key in seen:
            continue
        seen.add(key)
        c = row.counters
        if not c.get("principals"):
            continue
        out.append(
            {
                "route": row.route,
                "source": row.source,
                "requests": c["requests"],
                "status_4xx": c.get("status_4xx", 0),
                "status_5xx": c.get("status_5xx", 0),
                "principals": c["principals"],
            }
        )
    return out


def latest_route_signals(
    conn: sa.Connection, project_id: str, within: timedelta = timedelta(minutes=30)
) -> list[dict[str, Any]]:
    """Most recent score per (source, route) inside ``within``: input for RL RouteSignal.
    Attack-drill windows are excluded: a drill must not steer a real recommendation."""
    rows = conn.execute(
        sa.select(runtime_windows.c.score)
        .where(
            runtime_windows.c.project_id == project_id,
            runtime_windows.c.source != "drill",
            runtime_windows.c.window_end > utcnow() - within,
        )
        .order_by(runtime_windows.c.window_end.desc())
    ).scalars()
    seen: set[tuple[str, str]] = set()
    out = []
    for doc in rows:
        key = (doc["source"], doc["route"])
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "route": doc["route"],
                "alert_rate": doc["alert_rate"],
                "anomaly_score": doc["anomaly_score"],
                "source": doc["source"],
            }
        )
    return out
