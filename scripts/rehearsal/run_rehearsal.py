#!/usr/bin/env python3
"""Two-organization pilot rehearsal driver (local, no owner GitHub credentials).

Boot `make dev` (or the e2e stack) with:

    APP_ENV=development AUTH_DEV_LOGIN_ENABLED=true \
    ANTHROPIC_API_KEY=... SECURITY_SANDBOX=vercel \
    REPOSITORY_BINDINGS_FILE=scripts/rehearsal/bindings.json \
    SECURITY_AGENTS_WORKDIR=/tmp/rehearsal-work

Then run:  .venv/bin/python scripts/rehearsal/run_rehearsal.py [--bindings-file PATH]

Org A and Org B are two dev-login users; each gets a personal organization, so
cross-org reads exercise the real tenant boundary. The driver registers org A's
repo binding after connect (repo_id is generated server-side), creates and
self-approves a sandbox-only scope, runs a controlled assessment, and proves
org B cannot see org A's objects. Evidence lands in evidence/rehearsal-run.json.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"
DEFAULT_BINDINGS = Path(__file__).resolve().parent / "bindings.json"


def _req(base, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), e.headers.get("Set-Cookie")


def _login(base: str, login: str) -> dict:
    status, me, cookie = _req(base, "POST", "/api/v1/auth/dev/login", {"github_login": login})
    assert status == 200 and cookie, (status, me)
    session = cookie.split("hackatl_session=")[1].split(";")[0]
    return {"Cookie": f"hackatl_session={session}", "X-CSRF-Token": me["csrf_token"]}


def _auth(h, method, base, path, body=None):
    status, body_out, _ = _req(base, method, path, body, headers=h)
    return status, body_out


def _register_binding(bindings_file: Path, repo_id: str, local_path: Path) -> None:
    entries = json.loads(bindings_file.read_text() or "[]") if bindings_file.is_file() else []
    entries = [e for e in entries if e.get("repo_id") != repo_id]
    entries.append({"repo_id": repo_id, "local_path": str(local_path), "subdir": "."})
    bindings_file.write_text(json.dumps(entries, indent=2))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--repo-path", default=str(Path(__file__).resolve().parent / "target-app"))
    p.add_argument("--bindings-file", default=str(DEFAULT_BINDINGS))
    args = p.parse_args()
    base = args.base_url
    EVIDENCE_DIR.mkdir(exist_ok=True)
    results: list[dict] = []

    def record(step: str, ok: bool, detail) -> None:
        results.append({"step": step, "ok": ok, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {step}: {str(detail)[:160]}")

    # --- Org A -------------------------------------------------------------
    ha = _login(base, "rehearsal-org-a")
    s, proj_a = _auth(ha, "POST", base, "/api/v1/projects", {"name": "rehearsal-a"})
    record("org A project", s == 201, proj_a)
    if s != 201:
        return _finish(results, 1)
    pa = proj_a["id"]

    s, repo_a = _auth(
        ha, "POST", base, f"/api/v1/projects/{pa}/repositories",
        {"full_name": f"rehearsal/books-target-{int(time.time())}", "default_branch": "main", "installation_id": None},
    )
    record("org A repository connect", s == 201, repo_a)
    ra = repo_a.get("id")
    if not ra:
        return _finish(results, 1)

    _register_binding(Path(args.bindings_file), ra, Path(args.repo_path).resolve())
    record("org A repo binding registered", True, {"repo_id": ra})

    expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    s, scope_a = _auth(
        ha, "POST", base, f"/api/v1/projects/{pa}/scopes",
        {
            "name": "rehearsal-a-scope",
            "expires_at": expires,
            "targets": [{
                "kind": "sandbox_build",
                "repo_id": ra,
                "build_recipe_ref": "build_recipe.json",
                "exposed_ports": [8080],
            }],
            "allowed_check_categories": ["authorization.ownership", "access_control.role"],
        },
    )
    record("org A scope create", s == 201, scope_a)
    sa_scope = scope_a.get("id")
    if sa_scope:
        s2, appr = _auth(ha, "POST", base, f"/api/v1/scopes/{sa_scope}/approve")
        record("org A scope self-approve (sandbox-only)", s2 == 200, appr.get("status", appr))

    # The worker verifies commit_sha is a real commit in the bound checkout.
    import subprocess
    repo_path = Path(args.repo_path).resolve()
    if not (repo_path / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Adam Issac",
             "-c", "user.email=62719255+adamissac@users.noreply.github.com",
             "commit", "-qm", "rehearsal target app"],
            cwd=repo_path, check=True,
        )
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    s, asm_a = _auth(
        ha, "POST", base, f"/api/v1/projects/{pa}/assessments",
        {"scope_id": sa_scope, "repo_id": ra, "commit_sha": commit_sha},
    )
    record("org A assessment start (real provider+sandbox)", s == 202, asm_a)
    aid = (asm_a.get("assessment") or {}).get("id")
    if aid:
        cur = {}
        state = ""
        for _ in range(180):
            time.sleep(5)
            s, cur = _auth(ha, "GET", base, f"/api/v1/assessments/{aid}")
            state = str((cur.get("assessment") or {}).get("state") or "").lower()
            if state in {"completed", "failed", "cancelled"}:
                break
        err = ((cur.get("assessment") or {}).get("error") or {}).get("message")
        record("org A assessment finished", state == "completed", err or state)
        fnds = cur.get("findings") or []
        record("org A genuine findings", bool(fnds), [f.get("title") for f in fnds][:8])

    # --- Org B (isolation proof) --------------------------------------------
    hb = _login(base, "rehearsal-org-b")
    s, proj_b = _auth(hb, "POST", base, "/api/v1/projects", {"name": "rehearsal-b"})
    record("org B project", s == 201, proj_b)

    s, out = _auth(hb, "GET", base, f"/api/v1/projects/{pa}")
    record("org B cannot read org A project", s in (403, 404), s)
    s, out = _auth(hb, "GET", base, f"/api/v1/assessments/{aid or 'x'}")
    record("org B cannot read org A assessment", s in (403, 404), s)
    s, out = _auth(hb, "GET", base, f"/api/v1/projects/{pa}/repositories")
    record("org B cannot list org A repositories", s in (403, 404), s)

    # --- Ingest / defense-lab surface ---------------------------------------
    s, app_b = _auth(
        hb, "POST", base, f"/api/v1/projects/{proj_b.get('id')}/applications",
        {"name": "books staging", "environment": "owned_test"},
    )
    record("org B owned_test application", s == 201, app_b)

    return _finish(results, 0 if all(r["ok"] for r in results) else 1)


def _finish(results: list[dict], code: int) -> int:
    out = EVIDENCE_DIR / "rehearsal-run.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nEvidence: {out}")
    fails = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(fails)}/{len(results)} steps passed")
    return code or (1 if fails else 0)


if __name__ == "__main__":
    import sys
    sys.exit(main())
