"""Storage maintenance: bounded retention, a storage report, and threshold alerts.

Run as a sidecar next to the API/workers (`deploy/railway/start.sh`):

    python -m backend.maintenance loop     # report hourly, apply retention policy
    python -m backend.maintenance report   # one-shot JSON report (no writes)
    python -m backend.maintenance apply    # apply retention once (dry-run unless
                                           # RETENTION_DRY_RUN=false or --apply)

Design notes (docs/deploy/POSTGRES_STORAGE.md):

- Deletes are bounded: each table class is removed in `retention_batch_limit` batches
  inside short transactions, so a large first cleanup never holds one giant lock.
- `index_payloads`/`index_snapshots` carry FORCE RLS: their pruning runs inside a
  transaction that sets `ari.project_id` first, one project at a time.
- Nothing here touches audit_events (append-only, enforced by DB triggers), findings,
  approvals, authorized_scopes, or active assessments. `retention_assessment_detail_days`
  defaults to 0 (disabled) so pilot transcript evidence is never deleted by ordinary
  cleanup.
- Deleting rows frees space for reuse inside Postgres; it does not shrink the Railway
  volume. The report distinguishes "reclaimable inside Postgres" from disk that only a
  repack/restore could return (docs explain when each applies).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from backend import audit
from backend.db import (
    agent_runs,
    artifacts,
    assessment_events,
    assessments,
    get_engine,
    idempotency_keys,
    index_payloads,
    index_snapshots,
    jobs,
    monitoring_events,
    projects,
    reassessment_requests,
    runtime_windows,
    sessions,
    transaction,
    webhook_deliveries,
)
from backend.services import project_org
from backend.settings import Settings, get_settings
from shared.assessment import TERMINAL_STATES
from shared.audit import ActorType

log = logging.getLogger("backend.maintenance")

ALERT_THRESHOLDS_PCT = (70, 80, 90)

_TERMINAL_JOBS = ("succeeded", "failed", "cancelled", "dead")
# queued/started requests may still be feeding live work; only settled ones are history.
_TERMINAL_RRQ = ("skipped", "blocked_no_scope")


def _batch_delete(
    conn: Connection, table: sa.Table, where: sa.sql.ClauseElement, limit: int
) -> int:
    """Delete at most `limit` rows matching `where` in this transaction (ctid-batched)."""
    ctid = sa.literal_column("ctid")
    result = conn.execute(
        sa.delete(table).where(
            ctid.in_(sa.select(ctid).select_from(table).where(where).limit(limit))
        )
    )
    return result.rowcount or 0


def _days(n: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=n)


def _hours(n: int) -> datetime:
    return datetime.now(UTC) - timedelta(hours=n)


def _active_snapshot_ids(conn: Connection) -> set[str]:
    """snapshot_ids referenced by any non-terminal assessment — never pruned."""
    rows = conn.execute(
        sa.select(assessments.c.doc["snapshot_id"].as_string())
        .where(assessments.c.state.notin_([s.value for s in TERMINAL_STATES]))
        .where(assessments.c.doc["snapshot_id"].as_string().is_not(None))
    )
    return {r[0] for r in rows}


def _scope_tenant(conn: Connection, project_id: str) -> None:
    """Same RLS predicate the payload store uses (backend/intel_store.py)."""
    conn.execute(sa.text("SELECT set_config('ari.project_id', :pid, true)"), {"pid": project_id})


def _payload_prune_targets(
    conn: Connection, project_id: str, s: Settings
) -> tuple[set[str], set[str]]:
    """(orphan snapshot_ids, superseded ready snapshot_ids) for one project.

    Caller must already hold an RLS-scoped transaction (`_scope_tenant`).
    """
    keep = s.retention_snapshots_per_repo
    grace = _days(s.retention_snapshot_grace_days)
    orphan_grace = _hours(s.retention_orphan_grace_hours)

    ready_ids = set(
        conn.execute(
            sa.select(index_snapshots.c.snapshot_id).where(
                index_snapshots.c.project_id == project_id
            )
        ).scalars()
    )

    # Orphans: payload rows never marked ready and older than the orphan grace window
    # (covers failed/abandoned index jobs; an in-flight index run is younger than this).
    orphan_snapshots = set(
        conn.execute(
            sa.select(index_payloads.c.snapshot_id)
            .where(index_payloads.c.project_id == project_id)
            .where(index_payloads.c.created_at < orphan_grace)
            .where(index_payloads.c.snapshot_id.notin_(ready_ids) if ready_ids else sa.true())
            .distinct()
        ).scalars()
    )

    # Superseded: ready snapshots beyond `keep` per repo, older than the grace window,
    # and not referenced by any in-flight assessment.
    repo_ids = (
        conn.execute(
            sa.select(index_snapshots.c.repo_id)
            .where(index_snapshots.c.project_id == project_id)
            .distinct()
        )
        .scalars()
        .all()
    )
    protected = _active_snapshot_ids(conn)
    prunable_ready: set[str] = set()
    for rid in repo_ids:
        ranked = conn.execute(
            sa.select(index_snapshots.c.snapshot_id, index_snapshots.c.created_at)
            .where(index_snapshots.c.project_id == project_id)
            .where(index_snapshots.c.repo_id == rid)
            .order_by(index_snapshots.c.created_at.desc())
        ).all()
        for sid, created in ranked[keep:]:
            if sid not in protected and created < grace:
                prunable_ready.add(sid)
    return orphan_snapshots, prunable_ready


def _prune_index_payloads(
    engine: Engine, project_id: str, s: Settings, *, dry_run: bool
) -> dict[str, Any]:
    """Prune orphan + superseded ready payloads for one project.

    Every transaction touching index_payloads/index_snapshots re-sets ari.project_id
    (set_config(..., true) is transaction-local). Delete batches commit per batch so a
    large first cleanup never holds one giant lock.
    """
    batch = s.retention_batch_limit
    with transaction(engine) as conn:
        _scope_tenant(conn, project_id)
        orphans, superseded = _payload_prune_targets(conn, project_id, s)
        targets = sorted(orphans | superseded)
        if not targets:
            return {
                "payload_rows": 0,
                "payload_bytes": 0,
                "index_snapshot_rows": 0,
                "orphan_snapshots": 0,
                "superseded_snapshots": 0,
            }
        stats = conn.execute(
            sa.select(
                sa.func.count(),
                sa.func.coalesce(sa.func.sum(index_payloads.c.size_bytes), 0),
            )
            .where(index_payloads.c.project_id == project_id)
            .where(index_payloads.c.snapshot_id.in_(targets))
        ).first()
        snap_rows = conn.execute(
            sa.select(sa.func.count())
            .select_from(index_snapshots)
            .where(index_snapshots.c.snapshot_id.in_(targets))
        ).scalar_one()
        payload_rows, payload_bytes = int(stats[0]), int(stats[1])

    if not dry_run and targets:
        deleted = 0
        while True:
            with transaction(engine) as conn:
                _scope_tenant(conn, project_id)
                n = _batch_delete(
                    conn,
                    index_payloads,
                    sa.and_(
                        index_payloads.c.project_id == project_id,
                        index_payloads.c.snapshot_id.in_(targets),
                    ),
                    batch,
                )
            deleted += n
            if n < batch:
                break
        payload_rows = deleted
        if superseded:
            with transaction(engine) as conn:
                _scope_tenant(conn, project_id)
                conn.execute(
                    sa.delete(index_snapshots).where(
                        index_snapshots.c.snapshot_id.in_(sorted(superseded))
                    )
                )
        with transaction(engine) as conn:
            audit.record(
                conn,
                org_id=project_org(conn, project_id),
                project_id=project_id,
                actor_type=ActorType.system,
                actor_id="maintenance",
                action="maintenance.retention",
                subject_type="project",
                subject_id=project_id,
                details={
                    "dry_run": False,
                    "payload_rows": payload_rows,
                    "payload_bytes": payload_bytes,
                    "orphan_snapshots": len(orphans),
                    "superseded_snapshots": len(superseded),
                },
            )
    return {
        "payload_rows": payload_rows,
        "payload_bytes": payload_bytes,
        "index_snapshot_rows": snap_rows,
        "orphan_snapshots": len(orphans),
        "superseded_snapshots": len(superseded),
    }


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _prune_assessment_detail(conn: Connection, s: Settings, *, dry_run: bool) -> dict[str, int]:
    """Optional transcript pruning — disabled unless retention_assessment_detail_days > 0."""
    if s.retention_assessment_detail_days <= 0:
        return {}
    cutoff = _days(s.retention_assessment_detail_days)
    terminal_ids = sa.select(assessments.c.id).where(
        assessments.c.state.in_([st.value for st in TERMINAL_STATES])
    )
    out = {}
    for table in (assessment_events, agent_runs):
        if dry_run:
            out[table.name] = conn.execute(
                sa.select(sa.func.count())
                .select_from(table)
                .where(table.c.assessment_id.in_(terminal_ids))
                .where(table.c.created_at < cutoff)
            ).scalar_one()
        else:
            total = 0
            while True:
                n = _batch_delete(
                    conn,
                    table,
                    sa.and_(
                        table.c.assessment_id.in_(terminal_ids),
                        table.c.created_at < cutoff,
                    ),
                    s.retention_batch_limit,
                )
                total += n
                if n < s.retention_batch_limit:
                    break
            out[table.name] = total
    return out


def _du(path: Path) -> int:
    total = 0
    try:
        for dirpath, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _artifact_root(s: Settings) -> Path | None:
    parsed = urlparse(s.artifact_store_uri)
    if parsed.scheme not in ("file", ""):
        return None
    return Path((parsed.netloc + parsed.path) or ".").expanduser()


def _gc_artifact_orphans(conn: Connection, s: Settings, *, dry_run: bool) -> dict[str, int]:
    """Delete artifact files on disk with no artifacts row, older than the grace window."""
    root = _artifact_root(s)
    if root is None or not root.is_dir():
        return {}
    cutoff = time.time() - s.retention_orphan_artifact_hours * 3600
    files: list[tuple[str, Path]] = []
    base = root / "sha256"
    if base.is_dir():
        for sub in base.iterdir():
            if not sub.is_dir() or len(sub.name) != 2:
                continue
            for f in sub.iterdir():
                if f.is_file() and len(f.name) == 64 and f.stat().st_mtime < cutoff:
                    files.append((f.name, f))
    if not files:
        return {"artifact_orphan_files": 0, "artifact_orphan_bytes": 0}
    known = {
        r[0]
        for r in conn.execute(
            sa.select(artifacts.c.sha256).where(artifacts.c.sha256.in_([h for h, _ in files]))
        )
    }
    orphans = [(h, p) for h, p in files if h not in known]
    nbytes = sum(p.stat().st_size for _, p in orphans)
    if not dry_run:
        for _h, p in orphans:
            try:
                p.unlink()
            except OSError:
                pass
    return {"artifact_orphan_files": len(orphans), "artifact_orphan_bytes": nbytes}


def _gc_checkout_refs(s: Settings, *, dry_run: bool) -> dict[str, Any]:
    """Trim refs/ari/* inside each bare clone and repack so objects stop accumulating."""
    workdir = s.security_workdir
    if workdir is None:
        return {}
    repos_dir = workdir / "repos"
    if not repos_dir.is_dir():
        return {}
    keep = s.retention_checkout_refs_keep
    per_repo: dict[str, dict[str, int]] = {}
    for repo in sorted(repos_dir.iterdir()):
        if not repo.is_dir() or not (repo / "HEAD").exists():
            continue
        try:
            out = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "for-each-ref",
                    "--sort=-committerdate",
                    "--format=%(refname)",
                    "refs/ari/",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            ).stdout.split()
            stale = out[keep:]
            per_repo[repo.name] = {"refs": len(out), "prunable_refs": len(stale)}
            if dry_run or not stale:
                continue
            for ref in stale:
                subprocess.run(
                    ["git", "-C", str(repo), "update-ref", "-d", ref],
                    capture_output=True,
                    timeout=60,
                )
            subprocess.run(
                ["git", "-C", str(repo), "gc", "--prune=now", "--quiet"],
                capture_output=True,
                timeout=1800,
            )
            per_repo[repo.name]["bytes_after"] = _du(repo)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("checkout gc skipped %s: %s", repo, exc)
    return {"checkouts": per_repo}


def _table_stats(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        sa.text(
            """
            SELECT t.relname,
                   pg_total_relation_size(t.oid) AS total_bytes,
                   pg_relation_size(t.oid) AS relation_bytes,
                   pg_indexes_size(t.oid) AS indexes_bytes,
                   s.n_live_tup, s.n_dead_tup,
                   s.last_vacuum, s.last_autovacuum, s.last_analyze, s.last_autoanalyze
            FROM pg_stat_user_tables s
            JOIN pg_class t ON t.oid = s.relid
            ORDER BY total_bytes DESC
            """
        )
    ).mappings()
    return [
        {
            "table": r["relname"],
            "total_bytes": int(r["total_bytes"]),
            "relation_bytes": int(r["relation_bytes"]),
            "indexes_bytes": int(r["indexes_bytes"]),
            "live_tuples": int(r["n_live_tup"]),
            "dead_tuples": int(r["n_dead_tup"]),
            "last_vacuum": r["last_vacuum"].isoformat() if r["last_vacuum"] else None,
            "last_autovacuum": (r["last_autovacuum"].isoformat() if r["last_autovacuum"] else None),
        }
        for r in rows
    ]


def _try_scalar(conn: Connection, sql: str) -> int | None:
    try:
        return int(conn.execute(sa.text(sql)).scalar_one())
    except Exception:
        return None


def _jobs_breakdown(conn: Connection) -> dict[str, Any]:
    rows = conn.execute(
        sa.text(
            """
            SELECT status, count(*) AS n,
                   COALESCE(sum(pg_column_size(payload) + pg_column_size(result)
                                + pg_column_size(last_error)), 0) AS doc_bytes
            FROM jobs GROUP BY status ORDER BY n DESC
            """
        )
    )
    return {r[0]: {"rows": int(r[1]), "doc_bytes": int(r[2])} for r in rows}


def _payload_stats(engine: Engine, project_ids: list[str]) -> list[dict[str, Any]]:
    """Payload bytes by kind, aggregated per project under RLS scoping."""
    agg: dict[str, dict[str, int]] = {}
    for pid in project_ids:
        with transaction(engine) as conn:
            conn.execute(sa.text("SELECT set_config('ari.project_id', :pid, true)"), {"pid": pid})
            rows = conn.execute(
                sa.select(
                    index_payloads.c.kind,
                    sa.func.count(),
                    sa.func.coalesce(sa.func.sum(index_payloads.c.size_bytes), 0),
                ).group_by(index_payloads.c.kind)
            )
            for kind, n, nbytes in rows:
                e = agg.setdefault(kind, {"rows": 0, "bytes": 0})
                e["rows"] += int(n)
                e["bytes"] += int(nbytes)
    return [{"kind": k, **v} for k, v in sorted(agg.items(), key=lambda kv: -kv[1]["bytes"])]


def storage_report(engine: Engine | None = None, s: Settings | None = None) -> dict[str, Any]:
    """Full storage report: database, WAL, tables, jobs, payloads, filesystem, alerts."""
    s = s or get_settings()
    engine = engine or get_engine()
    with engine.connect() as conn:
        db_bytes = _try_scalar(conn, "SELECT pg_database_size(current_database())") or 0
        wal_bytes = _try_scalar(conn, "SELECT COALESCE(sum(size),0) FROM pg_ls_waldir()")
        autovacuum = conn.execute(sa.text("SHOW autovacuum")).scalar()
        long_running = _try_scalar(
            conn,
            "SELECT count(*) FROM pg_stat_activity WHERE state <> 'idle'"
            " AND now() - xact_start > interval '10 minutes'",
        )
        slots = _try_scalar(conn, "SELECT count(*) FROM pg_replication_slots")
        tables = _table_stats(conn)
        job_stats = _jobs_breakdown(conn)
        project_ids = [r[0] for r in conn.execute(sa.select(projects.c.id))]
        assessments_by_state = {
            r[0]: int(r[1])
            for r in conn.execute(
                sa.select(assessments.c.state, sa.func.count()).group_by(assessments.c.state)
            )
        }

    payload_stats = _payload_stats(engine, project_ids)
    fs: dict[str, Any] = {}
    root = _artifact_root(s)
    if root is not None:
        fs["artifact_root"] = str(root)
        fs["artifact_bytes"] = _du(root)
    if s.security_workdir is not None:
        fs["security_workdir"] = str(s.security_workdir)
        fs["workdir_bytes"] = _du(s.security_workdir)
        checkouts = _gc_checkout_refs(s, dry_run=True).get("checkouts", {})
        fs["checkouts"] = checkouts

    limit = s.postgres_volume_bytes
    used = db_bytes + (wal_bytes or 0)
    pct = round(100.0 * used / limit, 2) if limit > 0 else None
    alerts = [
        f"postgres usage at {pct:.1f}% of configured volume ({used}/{limit} bytes)"
        for t in ALERT_THRESHOLDS_PCT
        if pct is not None and pct >= t
    ]
    reclaim = run_retention(engine=engine, s=s, dry_run=True)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "database": {"name": engine.url.database, "size_bytes": db_bytes},
        "postgres_disk_bytes": used,
        "wal_bytes": wal_bytes,
        "autovacuum": autovacuum,
        "replication_slots": slots,
        "long_running_transactions": long_running,
        "volume": {"limit_bytes": limit or None, "used_bytes": used, "pct_used": pct},
        "alerts": alerts,
        "tables": tables,
        "jobs": job_stats,
        "assessments_by_state": assessments_by_state,
        "payloads_by_kind": payload_stats,
        "filesystem": fs,
        "reclaimable": reclaim["deleted"],
    }


def run_retention(
    engine: Engine | None = None,
    s: Settings | None = None,
    *,
    dry_run: bool | None = None,
) -> dict[str, Any]:
    """Apply bounded retention. `dry_run=None` uses settings.retention_dry_run."""
    s = s or get_settings()
    engine = engine or get_engine()
    if dry_run is None:
        dry_run = s.retention_dry_run
    batch = s.retention_batch_limit
    deleted: dict[str, Any] = {}

    classes: list[tuple[str, sa.Table, sa.sql.ClauseElement]] = [
        ("expired_sessions", sessions, sessions.c.expires_at < sa.func.now()),
        (
            "idempotency_keys",
            idempotency_keys,
            idempotency_keys.c.created_at < _days(s.retention_idempotency_days),
        ),
        (
            "terminal_jobs",
            jobs,
            sa.and_(
                jobs.c.status.in_(_TERMINAL_JOBS),
                jobs.c.updated_at < _days(s.retention_jobs_days),
            ),
        ),
        (
            "webhook_deliveries",
            webhook_deliveries,
            webhook_deliveries.c.received_at < _days(s.retention_webhook_deliveries_days),
        ),
        (
            "monitoring_events",
            monitoring_events,
            monitoring_events.c.created_at < _days(s.retention_monitoring_events_days),
        ),
        (
            "reassessment_requests",
            reassessment_requests,
            sa.and_(
                reassessment_requests.c.status.in_(_TERMINAL_RRQ),
                reassessment_requests.c.created_at < _days(s.retention_reassessment_requests_days),
            ),
        ),
        (
            "runtime_windows",
            runtime_windows,
            runtime_windows.c.window_end < _days(s.retention_runtime_windows_days),
        ),
    ]
    for name, table, where in classes:
        if dry_run:
            with transaction(engine) as conn:
                deleted[name] = conn.execute(
                    sa.select(sa.func.count()).select_from(table).where(where)
                ).scalar_one()
            continue
        total = 0
        while True:
            with transaction(engine) as conn:
                n = _batch_delete(conn, table, where, batch)
            total += n
            if n < batch:
                break
        deleted[name] = total

    # RLS-scoped payload pruning, one project at a time, batches committing per batch.
    with transaction(engine) as conn:
        project_ids = [r[0] for r in conn.execute(sa.select(projects.c.id))]
    payload_totals: dict[str, dict[str, Any]] = {}
    for pid in project_ids:
        res = _prune_index_payloads(engine, pid, s, dry_run=dry_run)
        if res["payload_rows"] or res["index_snapshot_rows"]:
            payload_totals[pid] = res

    with transaction(engine) as conn:
        detail = _prune_assessment_detail(conn, s, dry_run=dry_run)
        if detail:
            deleted.update(detail)
        orphans = _gc_artifact_orphans(conn, s, dry_run=dry_run)
        if orphans:
            deleted.update(orphans)
    if payload_totals:
        deleted["index_payloads"] = payload_totals
    checkouts = _gc_checkout_refs(s, dry_run=dry_run)
    if checkouts:
        deleted.update(checkouts)
    return {"dry_run": dry_run, "deleted": deleted}


def _post_alert(url: str, payload: dict[str, Any]) -> None:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as exc:  # alerting must never take maintenance down
        log.warning("alert webhook post failed: %s", exc)


def run_once(engine: Engine | None = None, s: Settings | None = None) -> dict[str, Any]:
    """Report + retention + alerting; one maintenance cycle."""
    s = s or get_settings()
    engine = engine or get_engine()
    report = storage_report(engine, s)
    pct = report["volume"]["pct_used"]
    level = 0
    if pct is not None:
        for t in ALERT_THRESHOLDS_PCT:
            if pct >= t:
                level = t
    if level:
        log.warning(
            "postgres storage alert: %.1f%% of %d-byte volume used (level %d%%)",
            pct,
            s.postgres_volume_bytes,
            level,
        )
    if s.ops_alert_webhook_url and level:
        _post_alert(
            s.ops_alert_webhook_url,
            {
                "service": "ari-ai-backend",
                "kind": "postgres_storage",
                "pct_used": pct,
                "used_bytes": report["postgres_disk_bytes"],
                "limit_bytes": s.postgres_volume_bytes,
                "level": level,
                "top_tables": [
                    {"table": t["table"], "total_bytes": t["total_bytes"]}
                    for t in report["tables"][:5]
                ],
                "reclaimable": report["reclaimable"],
                "at": report["generated_at"],
            },
        )
    retention = run_retention(engine=engine, s=s)
    if retention["deleted"]:
        log.info("retention %s", json.dumps(retention["deleted"], default=str)[:4000])
    report["retention"] = retention
    return report


def _loop(s: Settings) -> None:
    while True:
        try:
            run_once(s=s)
        except Exception:
            log.exception("maintenance cycle failed; retrying in %ds", s.maintenance_interval_s)
        time.sleep(s.maintenance_interval_s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.maintenance")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report")
    ap = sub.add_parser("apply", help="run retention once")
    ap.add_argument("--apply", action="store_true", help="override RETENTION_DRY_RUN")
    ap.add_argument("--dry-run", action="store_true", help="force dry-run")
    sub.add_parser("loop")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    if args.cmd == "report":
        print(json.dumps(storage_report(s=s), indent=2, default=str))
        return 0
    if args.cmd == "apply":
        dry = s.retention_dry_run
        if args.apply:
            dry = False
        if args.dry_run:
            dry = True
        print(json.dumps(run_retention(s=s, dry_run=dry), indent=2, default=str))
        return 0
    if args.cmd == "loop":
        if not s.maintenance_enabled:
            log.info("MAINTENANCE_ENABLED is false; exiting")
            return 0
        _loop(s)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
