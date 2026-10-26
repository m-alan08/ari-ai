"""Shadow-runtime job: rules + optional frozen v6, persist LivePolicyDecision.

The handler is idempotent on (project, application, window_end, source). It never
enqueues adapters or ``live_defense_execute`` jobs, never opens a
DETECTED→EXECUTING machine, never writes a ``LiveControl``, and never writes
RL training labels. A grant or control channel on the same application does
not change that.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend import audit
from backend.db import live_policy_decisions, monitoring_events, runtime_windows, transaction
from backend.live_defense.policy import decide_inference, freshness_ok, pick_rule_action
from backend.runtime_keys import runtime_event_key
from backend.services import project_org
from backend.worker import JobContext, handler
from monitoring.detect import WindowView, authorization_context, detect, view_from_window
from shared.audit import ActorType
from shared.base import utcnow
from shared.ids import IdPrefix, new_id
from shared.live_defense import (
    SHADOW_DECISION_CAVEAT,
    InferenceStatus,
    LivePolicyDecision,
    ShadowProducer,
)
from shared.monitoring import (
    AUTHENTICATED_RUNTIME_SOURCES,
    EventSource,
    EventStatus,
    EventType,
    MonitoringEvent,
    RouteWindow,
    RuntimeSource,
)

log = logging.getLogger("backend.live_defense.engine")


def process_shadow(
    project_id: str,
    *,
    application_id: str | None,
    window_end: datetime,
    source: RuntimeSource,
) -> dict[str, Any]:
    """Build and persist one shadow decision for an ingest window. Idempotent."""
    dedupe = f"{project_id}:{application_id or '-'}:{window_end.isoformat()}:{source}"
    with transaction() as conn:
        existing = conn.execute(
            sa.select(live_policy_decisions.c.id).where(
                live_policy_decisions.c.dedupe_key == dedupe
            )
        ).scalar()
        if existing:
            return {"decision_id": existing, "created": False}
        rows = (
            conn.execute(
                sa.select(runtime_windows)
                .where(
                    runtime_windows.c.project_id == project_id,
                    runtime_windows.c.window_end == window_end,
                    runtime_windows.c.source == source,
                    runtime_windows.c.application_id.is_not_distinct_from(application_id),
                )
                .order_by(runtime_windows.c.route)
            )
            .mappings()
            .all()
        )
        windows = [_window_view(row) for row in rows]
        # The exact anomaly events these windows produced, not whatever shares a timestamp.
        keys = [runtime_event_key(source, application_id, r["route"], window_end) for r in rows]
        event_ids = list(
            conn.execute(
                sa.select(monitoring_events.c.id).where(
                    monitoring_events.c.project_id == project_id,
                    monitoring_events.c.dedupe_key.in_(keys),
                )
            ).scalars()
        )
        hits = detect(windows)
        auth = authorization_context(windows)
        inference = decide_inference(
            source=source, windows=windows, hits=hits, auth=auth, window_end=window_end
        )
        rule_ids = [h.rule_id for h in hits if h.rule_id != "http_200_ownership_unknown"]
        new_events = _record_rule_events(
            conn, project_id=project_id, source=source, window_end=window_end, hits=hits
        )
        evidence = list(dict.fromkeys([*event_ids, *new_events]))
        if inference.status is InferenceStatus.ran:
            producer = ShadowProducer.v6_shadow
            action, node = inference.action, inference.node
        elif rule_ids:
            producer = ShadowProducer.rules
            action, node = pick_rule_action(hits)
        else:
            producer = ShadowProducer.abstention
            action, node = pick_rule_action(hits)
        decision = LivePolicyDecision(
            id=new_id(IdPrefix.lpd),
            project_id=project_id,
            application_id=application_id,
            created_at=utcnow(),
            window_end=window_end,
            telemetry_source=source,
            simulated=source not in AUTHENTICATED_RUNTIME_SOURCES,
            producer=producer,
            rules_ran=True,
            inference_ran=inference.status is InferenceStatus.ran,
            inference_status=inference.status,
            action=action,
            node=node,
            top_actions=inference.top_actions,
            q_margin=inference.q_margin,
            observation=inference.observation,
            policy_version=inference.policy_version,
            checkpoint_sha256=inference.checkpoint_sha256,
            abstention_reason=inference.reason
            if inference.status is not InferenceStatus.ran
            else None,
            observation_complete=bool(windows),
            feature_schema_ok=inference.feature_schema_ok,
            action_mask_valid=inference.action_mask_valid,
            freshness_ok=freshness_ok(window_end),
            authorization_context=auth,
            evidence_event_ids=evidence,
            rule_hits=rule_ids,
            principal_targets=list(inference.principal_targets or []),
            feature_mapping_note=inference.feature_mapping_note,
            caveat=SHADOW_DECISION_CAVEAT,
        )
        inserted = conn.execute(
            pg_insert(live_policy_decisions)
            .values(
                id=decision.id,
                project_id=project_id,
                application_id=application_id,
                window_end=window_end,
                source=source,
                producer=producer.value,
                inference_ran=decision.inference_ran,
                dedupe_key=dedupe,
                doc=decision.model_dump(mode="json"),
            )
            .on_conflict_do_nothing()
            .returning(live_policy_decisions.c.id)
        ).scalar()
        stored_id = inserted or existing or decision.id
        if inserted:
            audit.record(
                conn,
                org_id=project_org(conn, project_id),
                project_id=project_id,
                actor_type=ActorType.system,
                actor_id="live_defense.shadow",
                action="live_defense.shadow_decision",
                subject_type="live_policy_decision",
                subject_id=inserted,
                details={
                    "producer": producer.value,
                    "inference_ran": decision.inference_ran,
                    "inference_status": inference.status.value,
                    "action": action,
                    "enforcement": "NOT_EXECUTED",
                    "executed": False,
                },
            )
        if inserted:
            from backend.live_defense.first_responder import maybe_enqueue

            maybe_enqueue(
                conn,
                project_id=project_id,
                application_id=application_id,
                window_end=window_end,
                source=source,
                live_policy_decision_id=inserted,
                event_ids=evidence,
            )
        return {"decision_id": stored_id, "created": inserted is not None}


def _window_view(row: sa.RowMapping) -> WindowView:
    counters = RouteWindow.model_validate(row["counters"])
    score = row["score"] or {}
    return view_from_window(
        counters,
        source=row["source"],
        anomaly_score=float(score.get("anomaly_score") or 0),
    )


def _record_rule_events(
    conn: sa.Connection,
    *,
    project_id: str,
    source: RuntimeSource,
    window_end: datetime,
    hits: list[Any],
) -> list[str]:
    ids: list[str] = []
    simulated = source not in AUTHENTICATED_RUNTIME_SOURCES
    for hit in hits:
        if hit.rule_id == "http_200_ownership_unknown":
            # Ownership gap is persisted on the decision; it is not an IDOR finding.
            continue
        ev = MonitoringEvent(
            id=new_id(IdPrefix.evt),
            project_id=project_id,
            source=EventSource.runtime,
            type=EventType.log_anomaly,
            occurred_at=window_end,
            received_at=utcnow(),
            dedupe_key=f"shadow-rule:{source}:{hit.rule_id}:{hit.route or '*'}:{window_end.isoformat()}"[
                :500
            ],
            simulated=simulated,
            status=EventStatus.processed,
            status_reason=f"[{source}] rule {hit.rule_id}: {hit.reason}"[:500],
        )
        inserted = conn.execute(
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
            .returning(monitoring_events.c.id)
        ).scalar()
        if inserted:
            ids.append(inserted)
    return ids


@handler("live_defense_shadow")
def live_defense_shadow(ctx: JobContext) -> dict[str, Any]:
    payload = ctx.job.payload
    window_end = datetime.fromisoformat(payload["window_end"])
    return process_shadow(
        payload["project_id"],
        application_id=payload.get("application_id"),
        window_end=window_end,
        source=payload["source"],
    )


@handler("live_defense_execute")
def live_defense_execute(ctx: JobContext) -> dict[str, Any]:
    from backend.live_defense.controls import execute_control

    with transaction() as conn:
        return execute_control(conn, ctx.job.payload["control_id"], effect_guard=ctx.effect)


@handler("live_defense_verify")
def live_defense_verify(ctx: JobContext) -> dict[str, Any]:
    from backend.live_defense.controls import verify_job

    with transaction() as conn:
        return verify_job(conn, ctx.job.payload["control_id"])


@handler("live_defense_expire")
def live_defense_expire(ctx: JobContext) -> dict[str, Any]:
    from backend.live_defense.controls import expire_job

    with transaction() as conn:
        return expire_job(conn, ctx.job.payload["control_id"])


@handler("live_defense_rollback")
def live_defense_rollback(ctx: JobContext) -> dict[str, Any]:
    from backend.live_defense.controls import rollback_job

    with transaction() as conn:
        return rollback_job(conn, ctx.job.payload["control_id"])


@handler("live_defense_emergency_stop")
def live_defense_emergency_stop(ctx: JobContext) -> dict[str, Any]:
    from backend.live_defense.controls import emergency_stop_job

    with transaction() as conn:
        return emergency_stop_job(
            conn,
            ctx.job.payload["project_id"],
            ctx.job.payload.get("application_id"),
        )
