"""Run the labelled commit eval against the live pipeline.

Each case becomes one commit on its own branch (from origin/main); pushing it triggers the
normal GitHub App assessment. The scorer runs inside the platform (it needs DATABASE_URL),
reached through ``ARI_EVAL_SSH`` (a command that runs stdin as a shell script there, e.g.
``ssh -i key user@host``). Fix PRs the platform opens for eval branches are closed, and the
branches deleted, after each batch.

    python security-agents/evals/commits/run.py --batch 4 --out security-agents/evals/commits/results
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True).stdout


def push_case(case: dict, run_tag: str) -> str:
    branch = f"eval/{run_tag}/{case['id']}"
    git("switch", "-q", "-C", branch, "origin/main")
    for edit in case["edits"]:
        path = REPO / edit["path"]
        text = path.read_text()
        if text.count(edit["old"]) != 1:
            raise SystemExit(f"{case['id']}: edit does not apply exactly once to {edit['path']}")
        path.write_text(text.replace(edit["old"], edit["new"], 1))
    git("commit", "-q", "-am", case["message"])
    sha = git("rev-parse", "HEAD").strip()
    for attempt in range(3):
        if subprocess.run(["git", "-C", str(REPO), "push", "-q", "-u", "origin", branch]).returncode == 0:
            return sha
        time.sleep(5 * (attempt + 1))
    raise SystemExit(f"{case['id']}: push failed")


def score(cases: list[dict], shas: dict[str, str]) -> dict:
    payload = json.dumps({"cases": cases, "shas": shas})
    if os.environ.get("ARI_API_BASE"):
        last_err = ""
        for _ in range(6):
            proc = subprocess.run(
                [sys.executable, str(HERE / "score_api.py")],
                input=payload,
                capture_output=True,
                text=True,
            )
            line = next((ln for ln in proc.stdout.splitlines() if ln.startswith('{"results"')), None)
            if line:
                return json.loads(line)
            last_err = proc.stderr[-400:]
            time.sleep(10)  # transient API errors (deploys, 5xx) must not kill the run
        raise SystemExit(f"API scorer failed: {last_err}")
    ssh = shlex.split(os.environ["ARI_EVAL_SSH"])
    script = (
        "cat > /tmp/ari_eval_score.py <<'PY'\n"
        + (HERE / "score.py").read_text()
        + "\nPY\npython /tmp/ari_eval_score.py <<'JSON'\n"
        + payload
        + "\nJSON\n"
    )
    proc = None
    for _ in range(5):
        proc = subprocess.run([*ssh, "sh"], input=script, capture_output=True, text=True)
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith('{"results"')), None)
        if line:
            return json.loads(line)
        time.sleep(10)
    raise SystemExit(f"scorer failed: {proc.stderr[-400:] if proc else ''}")


def cleanup(run_tag: str, cases: list[dict]) -> None:
    try:
        prs = json.loads(
            subprocess.run(
                ["gh", "pr", "list", "--state", "open", "--json", "number,headRefName"],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        # gh needs interactive auth; without it skip PR housekeeping — branch
        # deletion below still runs and stale ari/fix-* PRs can be closed later.
        print(f"  cleanup: gh unavailable ({exc}); skipping PR close", flush=True)
        prs = []
    for pr in prs:
        if pr["headRefName"].startswith("ari/fix-"):
            subprocess.run(
                [
                    "gh",
                    "pr",
                    "close",
                    str(pr["number"]),
                    "--delete-branch",
                    "--comment",
                    "Closed: opened by the labelled commit eval (the defect is not on main).",
                ],
                cwd=REPO,
                capture_output=True,
            )
    branches = [f"eval/{run_tag}/{c['id']}" for c in cases]
    subprocess.run(
        ["git", "-C", str(REPO), "push", "-q", "origin", "--delete", *branches], capture_output=True
    )


def validate_cases(cases: list[dict]) -> list[str]:
    """Missing case fields must fail loudly, never score as zero."""
    bad = []
    for c in cases:
        missing = [k for k in ("id", "kind", "files") if not c.get(k)]
        if missing:
            bad.append(f"{c.get('id', '<no id>')}: missing {', '.join(missing)}")
        for e in c.get("edits", []):
            if not e.get("path") or "old" not in e or "new" not in e:
                bad.append(f"{c.get('id')}: malformed edit entry")
                break
    return bad


def summarize(results: list[dict]) -> dict:
    """Rates are computed over SCORED cases only — a FAILED / CANCELLED /
    BUDGET_EXHAUSTED / still-running assessment lands in ``unscored`` with its
    reason and is never counted as a detection miss."""

    def rate(rows: list[dict], key: str) -> str:
        return f"{sum(bool(r.get(key)) for r in rows)}/{len(rows)}"

    scored = [r for r in results if r.get("scored")]
    unscored = [
        {"case_id": r["case_id"], "state": r["state"], "reason": r.get("unscored_reason")}
        for r in results
        if not r.get("scored")
    ]
    idor = [r for r in scored if r["kind"] == "idor"]
    vuln = [r for r in scored if r["kind"] == "vuln"]
    benign = [r for r in scored if r["kind"] == "benign"]
    seconds = sorted(r["seconds"] for r in scored if "seconds" in r)
    return {
        "scored_cases": f"{len(scored)}/{len(results)}",
        "unscored": unscored,
        "idor_validated": rate(idor, "validated"),
        "idor_fixed": rate(idor, "fixed"),
        "vuln_flagged": rate(vuln, "flagged"),
        "vuln_validated": rate(vuln, "validated"),
        "benign_false_positive": rate(benign, "validated"),
        "max_seconds": seconds[-1] if seconds else None,
        "median_seconds": seconds[len(seconds) // 2] if seconds else None,
        "over_180s": sum(s > 180 for s in seconds),
        "total_usd": round(sum(r.get("usd") or 0 for r in results), 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=4, help="cases pushed at once")
    ap.add_argument("--only", nargs="*", help="case ids")
    ap.add_argument("--out", type=Path, default=HERE / "results")
    a = ap.parse_args()
    cases = json.loads((HERE / "cases.json").read_text())["cases"]
    bad = validate_cases(cases)
    if bad:
        raise SystemExit("cases.json is not scoreable; fix before running:\n  " + "\n  ".join(bad))
    if a.only:
        cases = [c for c in cases if c["id"] in set(a.only)]
    if subprocess.run(["git", "-C", str(REPO), "diff", "--quiet"]).returncode:
        raise SystemExit("working tree has changes; commit or stash first")
    start = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    git("fetch", "-q", "origin")
    run_tag = time.strftime("%Y%m%d-%H%M%S")
    results: list[dict] = []
    try:
        for i in range(0, len(cases), a.batch):
            batch = cases[i : i + a.batch]
            shas = {c["id"]: push_case(c, run_tag) for c in batch}
            print(f"batch {i // a.batch + 1}: pushed {', '.join(shas)}", flush=True)
            deadline = time.monotonic() + 900
            report = None
            while True:
                try:
                    report = score(batch, shas)
                except SystemExit as exc:
                    print(f"  scorer failure (will retry until deadline): {exc}", flush=True)
                if (report and report["pending"] == 0) or time.monotonic() > deadline:
                    break
                time.sleep(20)
            if report is None:
                raise SystemExit(f"scorer never recovered for batch {batch}")
            for r in report["results"]:
                verdict = (
                    f"flagged={r.get('flagged')} validated={r.get('validated')} fixed={r.get('fixed')}"
                    if r.get("scored")
                    else f"UNSCORED ({r.get('unscored_reason')})"
                )
                print(
                    f"  {r['case_id']:<38} {r['state']:<10} {r.get('seconds')}s {verdict}",
                    flush=True,
                )
            results.extend(report["results"])
            cleanup(run_tag, batch)
    finally:
        git("switch", "-q", start)
    summary = summarize(results)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / f"{run_tag}.json").write_text(
        json.dumps({"summary": summary, "results": results}, indent=2) + "\n"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
