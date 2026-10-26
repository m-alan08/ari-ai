"""Durable billable-request ledger on first-responder invocation JSONB.

Devin Reliability (`ctx.effect`) owns generic job effects and Alembic 0016.
This ledger is the 7C adapter: one reservation per provider `complete()`,
persisted before the request is sent. Unresolved attempts are never
auto-replayed. Canonical intel and other read-only ops are not recorded.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend.db import first_responder_invocations, transaction
from backend.db import jobs as jobs_table
from shared.base import utcnow
from shared.live_defense import (
    AgentExecutionRecord,
    FirstResponderInvocation,
    ProviderAttempt,
    ProviderAttemptState,
)

UNRESOLVED = frozenset(
    {
        ProviderAttemptState.reserved,
        ProviderAttemptState.in_flight,
        ProviderAttemptState.uncertain,
    }
)


def _load(conn: Connection, invocation_id: str) -> FirstResponderInvocation | None:
    row = (
        conn.execute(
            sa.select(first_responder_invocations)
            .where(first_responder_invocations.c.id == invocation_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return FirstResponderInvocation.model_validate(row["doc"])


def _save(conn: Connection, inv: FirstResponderInvocation) -> FirstResponderInvocation:
    conn.execute(
        sa.update(first_responder_invocations)
        .where(first_responder_invocations.c.id == inv.id)
        .values(doc=inv.model_dump(mode="json"))
    )
    return inv


def _with_execution(inv: FirstResponderInvocation) -> AgentExecutionRecord:
    if inv.execution is not None:
        return inv.execution
    return AgentExecutionRecord(started_at=utcnow())


def unresolved_attempts(inv: FirstResponderInvocation) -> list[ProviderAttempt]:
    execution = inv.execution
    if execution is None:
        return []
    return [item for item in execution.provider_attempts if item.status in UNRESOLVED]


def has_unresolved_billable_attempt(inv: FirstResponderInvocation) -> bool:
    return bool(unresolved_attempts(inv)) or bool(
        inv.execution.uncertain_provider_outcome if inv.execution else False
    )


def should_refuse_replay(inv: FirstResponderInvocation) -> bool:
    """True when a previous billable outcome is unknown. In-flight is not this."""
    if inv.execution is not None and inv.execution.uncertain_provider_outcome:
        return True
    return any(item.status is ProviderAttemptState.uncertain for item in unresolved_attempts(inv))


def has_in_flight_attempt(inv: FirstResponderInvocation) -> bool:
    return any(
        item.status in {ProviderAttemptState.reserved, ProviderAttemptState.in_flight}
        for item in unresolved_attempts(inv)
    )


def reserve_attempt(invocation_id: str, *, model_id: str | None) -> ProviderAttempt:
    """Persist in_flight before the provider request is sent."""
    with transaction() as conn:
        inv = _load(conn, invocation_id)
        if inv is None:
            raise RuntimeError(f"missing invocation {invocation_id}")
        if has_unresolved_billable_attempt(inv):
            raise RuntimeError("UNCERTAIN_PROVIDER_OUTCOME: previous billable request is unknown")
        execution = _with_execution(inv)
        seq = len(execution.provider_attempts) + 1
        attempt_id = f"att_{invocation_id[-16:]}_{seq:04d}"
        attempt = ProviderAttempt(
            id=attempt_id,
            idempotency_key=f"fr-provider:{invocation_id}:{seq}",
            status=ProviderAttemptState.in_flight,
            reserved_at=utcnow(),
            sent_at=utcnow(),
            model_id=model_id,
            effect_key=f"provider:{attempt_id}",
        )
        updated = inv.model_copy(
            update={
                "execution": execution.model_copy(
                    update={"provider_attempts": [*execution.provider_attempts, attempt]}
                )
            }
        )
        _save(conn, updated)
        return attempt


def _patch_attempt(invocation_id: str, attempt_id: str, **fields: Any) -> ProviderAttempt | None:
    with transaction() as conn:
        inv = _load(conn, invocation_id)
        if inv is None or inv.execution is None:
            return None
        attempts: list[ProviderAttempt] = []
        found: ProviderAttempt | None = None
        uncertain = inv.execution.uncertain_provider_outcome
        for item in inv.execution.provider_attempts:
            if item.id == attempt_id:
                found = item.model_copy(update=fields)
                attempts.append(found)
                if found.status is ProviderAttemptState.uncertain:
                    uncertain = True
            else:
                attempts.append(item)
        if found is None:
            return None
        updated = inv.model_copy(
            update={
                "execution": inv.execution.model_copy(
                    update={
                        "provider_attempts": attempts,
                        "uncertain_provider_outcome": uncertain,
                    }
                )
            }
        )
        _save(conn, updated)
        return found


def settle_attempt(
    invocation_id: str,
    attempt_id: str,
    *,
    response_id: str | None,
    model_id: str | None,
    usage: Any | None,
    cost_usd: float | None,
) -> None:
    fields: dict[str, Any] = {
        "status": ProviderAttemptState.settled,
        "settled_at": utcnow(),
        "response_id": response_id,
        "model_id": model_id,
        "cost_usd": cost_usd,
        "error_code": None,
    }
    if usage is not None:
        fields["input_tokens"] = int(getattr(usage, "input_tokens", 0) or 0)
        fields["output_tokens"] = int(getattr(usage, "output_tokens", 0) or 0)
        fields["cache_creation_input_tokens"] = int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        )
    _patch_attempt(invocation_id, attempt_id, **fields)


def mark_attempt_uncertain(invocation_id: str, attempt_id: str, *, error_code: str) -> None:
    _patch_attempt(
        invocation_id,
        attempt_id,
        status=ProviderAttemptState.uncertain,
        settled_at=utcnow(),
        error_code=error_code,
    )
    # Bridge to the reliability recovery path (Alembic 0016): the worker may die
    # before this commit is visible to its run loop, so flag the owning job row
    # too. Reconcile then parks it ``recovery_required`` — the uncertain billable
    # outcome survives the crash and is never silently requeued for replay.
    with transaction() as conn:
        conn.execute(
            sa.update(jobs_table)
            .where(
                jobs_table.c.payload["invocation_id"].as_string() == invocation_id,
                jobs_table.c.status == "running",
                jobs_table.c.effect_state.is_(None),
            )
            .values(
                effect_state="in_flight",
                effect_key=attempt_id,
                updated_at=sa.func.now(),
            )
        )


def mark_attempt_cancelled(invocation_id: str, attempt_id: str) -> None:
    _patch_attempt(
        invocation_id,
        attempt_id,
        status=ProviderAttemptState.cancelled,
        settled_at=utcnow(),
        error_code="CANCELLED",
    )
