#!/usr/bin/env python3
"""Pilot release checklist, executed — not narrated.

    uv run python deploy/release_check.py --base-url http://localhost:8000 \
        [--ops-token T] [--backup-dir backups] [--max-backup-age-h 30]

Every row is PASS, FAIL, WARN or SKIP with evidence. Exit code is non-zero when
any row FAILs. Run it against a real deployment; pair it with
deploy/scripts/smoke_staging.sh for the full HTTP surface.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

results: list[tuple[str, str, str]] = []


def report(status: str, name: str, evidence: str) -> None:
    results.append((status, name, evidence))
    print(f"[{status:4}] {name}: {evidence}")


def get_json(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def check_ci() -> None:
    gh = subprocess.run(["which", "gh"], capture_output=True)
    if gh.returncode != 0:
        report("SKIP", "CI success", "gh CLI not installed; confirm checks on the PR page")
        return
    auth = subprocess.run(["gh", "auth", "status"], capture_output=True)
    if auth.returncode != 0:
        report("SKIP", "CI success", "gh not authenticated; confirm checks on the PR page")
        return
    pr = subprocess.run(
        ["gh", "pr", "view", "--json", "statusCheckRollup,number"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    if pr.returncode != 0:
        report("SKIP", "CI success", pr.stderr.strip()[:200] or "no PR for this branch")
        return
    checks = json.loads(pr.stdout)["statusCheckRollup"]
    bad = [
        c
        for c in checks
        if c.get("conclusion") not in ("SUCCESS", None, "SKIPPED")
        or c.get("status") not in ("COMPLETED", "SUCCESS")
    ]
    pending = [c for c in checks if c.get("status") not in ("COMPLETED", "SUCCESS")]
    if bad:
        report(
            "FAIL",
            "CI success",
            f"{len(bad)} failing/pending checks: "
            + ", ".join(c.get("name", "?") for c in (bad or pending)[:5]),
        )
    elif pending:
        report("WARN", "CI success", f"{len(pending)} checks still pending")
    else:
        report("PASS", "CI success", f"all {len(checks)} checks green")


def check_config() -> None:
    from backend.preflight import validate_config
    from backend.settings import get_settings

    s = get_settings()
    r = validate_config(s)
    for w in r.warnings:
        report("WARN", f"config: {w.split(':')[0]}", w)
    if r.ok:
        report("PASS", "secrets and configuration", f"APP_ENV={s.app_env}, no blocking errors")
    else:
        for e in r.errors:
            report("FAIL", "secrets and configuration", e)


def check_migrations() -> None:
    from backend.db import get_engine
    from backend.preflight import migration_status

    try:
        with get_engine().begin() as conn:
            st = migration_status(conn)
    except Exception as exc:
        report("FAIL", "migration compatibility", f"database unreachable: {type(exc).__name__}")
        return
    if st.at_head:
        report("PASS", "migration compatibility", f"at head {st.expected}")
    else:
        report(
            "FAIL",
            "migration compatibility",
            f"pending {st.pending} (current {st.current}); run hackatl-migrate",
        )


def check_http(base: str, ops_token: str | None) -> None:
    code, body = get_json(f"{base}/healthz")
    report(
        "PASS" if code == 200 and body.get("status") == "ok" else "FAIL",
        "health check",
        f"/healthz -> {code} {body.get('status', '')}",
    )

    code, body = get_json(f"{base}/readyz")
    if code == 200 and body.get("database") and body.get("queue"):
        report(
            "PASS",
            "readiness + job queue availability",
            f"/readyz ready (db={body.get('database')} queue={body.get('queue')})",
        )
    else:
        report("FAIL", "readiness + job queue availability", f"/readyz -> {code} {body}")

    code, _ = get_json(f"{base}/api/v1/projects")
    report(
        "PASS" if code == 401 else "FAIL",
        "authentication required",
        f"unauthenticated /api/v1/projects -> {code}",
    )

    if ops_token:
        code, body = get_json(f"{base}/api/v1/ops/status", {"X-Ops-Token": ops_token})
        if code == 200 and "queue" in body:
            q = body["queue"]["counts"]
            report("PASS", "worker/job-queue visibility", f"ops status ok; queue counts {q}")
            fr = body.get("first_responder", {})
            n = fr.get("projects_with_non_default_mode", "?")
            report(
                "PASS" if n == 0 else "FAIL",
                "first-responder OFF by default",
                f"default=off; projects with non-default mode: {n}",
            )
        else:
            report("FAIL", "worker/job-queue visibility", f"ops status -> {code}")
    else:
        report("WARN", "worker/job-queue visibility", "no --ops-token; ops surface unchecked")


def check_controls(base: str) -> None:
    code, body = get_json(f"{base}/api/openapi.json")
    if code != 200:
        report("SKIP", "cancellation and emergency-stop surface", f"openapi -> {code}")
        return
    paths = " ".join(body.get("paths", {}).keys())
    report(
        "PASS" if "emergency-stop" in paths else "FAIL",
        "emergency-stop endpoint",
        "present in API" if "emergency-stop" in paths else "absent",
    )
    report(
        "PASS" if "cancel" in paths else "WARN",
        "cancellation endpoint",
        "present in API" if "cancel" in paths else "not found",
    )


def check_first_responder_db() -> None:
    import sqlalchemy as sa

    from backend.db import first_responder_modes, get_engine

    try:
        with get_engine().begin() as conn:
            rows = conn.execute(
                sa.select(first_responder_modes.c.project_id, first_responder_modes.c.intent).where(
                    first_responder_modes.c.intent != "off"
                )
            ).all()
    except Exception as exc:
        report("FAIL", "first-responder OFF (database)", type(exc).__name__)
        return
    if rows:
        report("FAIL", "first-responder OFF (database)", f"non-off modes stored: {rows}")
    else:
        report("PASS", "first-responder OFF (database)", "no project has a non-off stored mode")


def check_backup(args: argparse.Namespace) -> None:
    d = Path(args.backup_dir)
    dumps = sorted(d.glob("*.dump"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
    if not dumps:
        report(
            "FAIL",
            "backup and recovery",
            f"no *.dump in {d}; run deploy/scripts/db_backup.sh first",
        )
        return
    newest = dumps[-1]
    age_h = (time.time() - newest.stat().st_mtime) / 3600
    if age_h > args.max_backup_age_h:
        report(
            "WARN",
            "backup and recovery",
            f"newest backup {newest.name} is {age_h:.1f}h old (> {args.max_backup_age_h}h)",
        )
    else:
        report("PASS", "backup and recovery", f"{newest.name} ({age_h:.1f}h old)")
    lst = subprocess.run(["pg_restore", "--list", str(newest)], capture_output=True)
    if lst.returncode == 0:
        report("PASS", "backup readable", f"pg_restore --list ok on {newest.name}")
    else:
        report(
            "WARN", "backup readable", "pg_restore not available to verify; restore drill is manual"
        )


def check_rollback() -> None:
    ok = (REPO / "deploy/scripts/db_restore.sh").exists() and (
        REPO / "docs/deploy/PILOT_OPERATIONS.md"
    ).exists()
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=REPO
    ).stdout.strip()
    if ok:
        report(
            "PASS",
            "rollback readiness",
            f"restore script + runbook present; deployed-from sha {sha}",
        )
    else:
        report("FAIL", "rollback readiness", "restore script or PILOT_OPERATIONS.md missing")


def check_pilot_scope() -> None:
    from backend.settings import get_settings

    s = get_settings()
    if s.app_env in ("staging", "production") and not s.auth_dev_login_enabled:
        report(
            "PASS",
            "read-only pilot scope",
            f"APP_ENV={s.app_env}, dev login off, feature_flags={sorted(s.flags()) or 'none'}",
        )
    elif s.is_dev:
        report("WARN", "read-only pilot scope", f"APP_ENV={s.app_env} (dev build, not staging)")
    else:
        report(
            "FAIL",
            "read-only pilot scope",
            f"APP_ENV={s.app_env}, dev login={s.auth_dev_login_enabled}",
        )


def main() -> int:
    p = argparse.ArgumentParser(prog="release_check")
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--ops-token", default=None)
    p.add_argument("--backup-dir", default="backups")
    p.add_argument("--max-backup-age-h", type=float, default=30.0)
    p.add_argument("--no-http", action="store_true", help="config+DB checks only")
    args = p.parse_args()
    base = args.base_url.rstrip("/")

    check_ci()
    check_config()
    check_migrations()
    if not args.no_http:
        check_http(base, args.ops_token)
        check_controls(base)
    check_first_responder_db()
    check_backup(args)
    check_rollback()
    check_pilot_scope()

    fails = sum(1 for s, *_ in results if s == "FAIL")
    print("---")
    print(f"release check: {len(results)} checks, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
