"""Trusted controller; candidate code is never imported or executed here."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.parse
from pathlib import Path

from scripts.ari_review.common import (
    BOT,
    GitHub,
    collect_ci,
    comments,
    fingerprint,
    journal,
    native_findings,
    sha,
    state_body,
    trusted_owners_guard,
    utc_day,
    validate_review,
    verdict,
    workstream,
)
from scripts.ari_review.ledger import Ledger

POLICY_PATH = Path(".github/ari-review/policy.json")
EVIDENCE_MARKER = "<!-- ari-review-evidence-v1\n"
SUMMARY_MARKER = "<!-- ari-release-summary-v1 -->"


def git(*args: str, cwd: Path | str | None = None, check: bool = True):
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=check,
    )


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def output(key: str, value: str) -> None:
    if "\n" in value:
        raise ValueError("unsafe workflow output")
    with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
        handle.write(f"{key}={value}\n")


def upstream(api: GitHub, policy: dict) -> str:
    return sha(api.call(f"/repos/{policy['canonical_repository']}/commits/main")["sha"])


def pull(api: GitHub, policy: dict) -> dict:
    pr = api.call(f"/repos/{policy['integration_repository']}/pulls/{policy['integration_pr']}")
    if (
        pr["state"] != "open"
        or pr["head"]["repo"]["full_name"] != policy["integration_repository"]
        or pr["head"]["ref"] != policy["integration_branch"]
    ):
        raise ValueError("PR/branch/repository is outside the configured trust boundary")
    return pr


def policy_digest() -> str:
    paths = sorted(
        [
            *Path(".github/ari-review").rglob("*"),
            *Path("scripts/ari_review").glob("*.py"),
            *Path(".github/workflows").glob("ari-*.yml"),
        ]
    )
    return hashlib.sha256(
        b"".join(str(p).encode() + b"\0" + p.read_bytes() for p in paths if p.is_file())
    ).hexdigest()


def prepare(policy: dict, directory: Path) -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    if "pull_request" in event and event["pull_request"]["number"] != policy["integration_pr"]:
        return
    api = GitHub()
    pr = pull(api, policy)
    if (
        event.get("pull_request", {}).get("head", {}).get("sha", pr["head"]["sha"])
        != pr["head"]["sha"]
    ):
        raise ValueError("superseded event; do not review a different mutable head")
    pin = {
        "version": 1,
        "candidate_sha": sha(pr["head"]["sha"]),
        "upstream_sha": upstream(api, policy),
        "policy_sha": sha(git("rev-parse", "HEAD").stdout.strip()),
        "policy_digest": policy_digest(),
        "run_id": os.environ["GITHUB_RUN_ID"],
        "regression_sha": sha(policy["trusted_regression_sha"]),
        "transfers": {},
        "pr_heads": {},
    }
    for owner, branch in policy["transfer_branches"].items():
        ref = urllib.parse.quote(branch, safe="")
        pin["transfers"][owner] = sha(
            api.call(f"/repos/{policy['integration_repository']}/branches/{ref}")["commit"]["sha"]
        )
    for number in (412, 458):
        pin["pr_heads"][str(number)] = sha(
            api.call(f"/repos/{policy['canonical_repository']}/pulls/{number}")["head"]["sha"]
        )
    pin["workstream_heads"] = {}
    for owner, config in policy["workstreams"].items():
        if "pull_request" in config:
            source = api.call(
                f"/repos/{policy['integration_repository']}/pulls/{config['pull_request']}"
            )
            if (
                source["head"]["ref"] != config["branch"]
                or source["head"]["repo"]["full_name"] != policy["integration_repository"]
            ):
                raise ValueError("workstream branch/PR mapping changed")
            pin["workstream_heads"][owner] = sha(source["head"]["sha"])
    write(directory / "pin.json", pin)
    output("candidate_sha", pin["candidate_sha"])
    output("policy_sha", pin["policy_sha"])
    ledger = Ledger(api, policy)
    _, ledger_state = ledger.load()
    cycles = ledger_state["cycles"]
    ci_allowed = (
        not any(r.get("candidate_sha") == pin["candidate_sha"] for r in cycles)
        and sum(r.get("day") == utc_day() for r in cycles) < policy["max_reviews_per_day"]
    )
    ci_allowed = ci_allowed and os.environ.get("GITHUB_RUN_ATTEMPT", "1") == "1"
    if ci_allowed:
        ledger.reserve("cycles", {"candidate_sha": pin["candidate_sha"], "day": utc_day()})
        api.call(
            f"/repos/{policy['integration_repository']}/issues/{policy['integration_pr']}/comments",
            "POST",
            {
                "body": state_body(
                    "<!-- ari-cycle-reservation-v1\n", {**pin, "day": utc_day()}, policy
                )
            },
        )
    output("ci_allowed", str(ci_allowed).lower())
    enabled = os.environ.get("ARI_REVIEW_API_ENABLED") == "true" and trusted_owners_guard(
        api, policy
    )
    allowed = not any(r["candidate_sha"] == pin["candidate_sha"] for r in ledger_state["reviews"])
    reason = "reserved" if allowed else "SHA already reserved; no automatic paid retries"
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        allowed, reason = False, "automatic paid retries disabled"
    allowed = allowed and enabled and ci_allowed
    if not ci_allowed:
        reason = "candidate cycle already reserved or daily CI limit reached"
    if not enabled:
        reason = "OpenAI API path disabled or credential missing; native review is supplementary"
    if allowed:
        record = {**pin, "day": utc_day(), "reserved_usd": policy["max_usd_per_review"]}
        ledger.reserve(
            "reviews",
            {
                "candidate_sha": pin["candidate_sha"],
                "day": utc_day(),
                "reserved_usd": policy["max_usd_per_review"],
            },
        )
        api.call(
            f"/repos/{policy['integration_repository']}/issues/{policy['integration_pr']}/comments",
            "POST",
            {"body": state_body("<!-- ari-review-reservation-v1\n", record, policy)},
        )
    write(directory / "admission.json", {"allowed": allowed, "reason": reason})
    output("candidate_sha", pin["candidate_sha"])
    output("policy_sha", pin["policy_sha"])
    output("audit_allowed", str(allowed).lower())


def production_tree(repo: Path, ref: str) -> dict:
    rows = git("ls-tree", "-r", "--full-tree", sha(ref), cwd=repo).stdout.splitlines()
    roots = (
        "backend/",
        "shared/",
        "security-agents/",
        "monitoring/",
        "code-review/",
        "code-intelligence/",
        "rl-defense/",
        "frontend/",
        "deploy/",
    )
    found = {}
    for row in rows:
        meta, path = row.split("\t", 1)
        if (
            path.startswith(roots)
            and not any(
                part in {"tests", "__pycache__", "node_modules"} for part in Path(path).parts
            )
            and not path.endswith(".md")
        ):
            found[path] = meta
    return found


def merge_evidence(repo: Path, pin: dict) -> dict:
    run = git(
        "merge-tree",
        "--write-tree",
        "--messages",
        sha(pin["upstream_sha"]),
        sha(pin["candidate_sha"]),
        cwd=repo,
        check=False,
    )
    candidate = production_tree(repo, pin["candidate_sha"])
    manifest = production_tree(repo, pin["transfers"]["manifest"])
    difference = sorted(
        p for p in candidate.keys() | manifest.keys() if candidate.get(p) != manifest.get(p)
    )
    return {
        "candidate_sha": pin["candidate_sha"],
        "upstream_sha": pin["upstream_sha"],
        "upstream_merge_clean": run.returncode == 0,
        "merge_exit": run.returncode,
        "merge_output": run.stdout[:50000],
        "manifest_sha": pin["transfers"]["manifest"],
        "manifest_production_equal": not difference,
        "production_differences": difference[:500],
        "production_diff_complete": len(difference) <= 500,
    }


def context(policy: dict, directory: Path, candidate: Path) -> None:
    pin = json.loads((directory / "pin.json").read_text())
    api = GitHub()
    evidence = {
        "pin": pin,
        "ci": collect_ci(api, pin, policy),
        "native_findings": native_findings(api, pin, policy),
        "sources": {},
        "complete": True,
    }
    for path in policy["source_paths"]:
        result = git("show", pin["candidate_sha"] + ":" + path, cwd=candidate, check=False)
        if result.returncode:
            evidence["sources"][path] = {"missing": True}
            evidence["complete"] = False
        else:
            evidence["sources"][path] = result.stdout
    for name in ("receipt.json", "merge.json", "admission.json"):
        file = directory / name
        evidence[name] = json.loads(file.read_text()) if file.exists() else {"missing": True}
    changed = git(
        "diff",
        "--no-ext-diff",
        "--unified=12",
        pin["upstream_sha"],
        pin["candidate_sha"],
        "--",
        "backend/backend",
        "shared/shared",
        "security-agents/security_agents",
        "deploy",
        cwd=candidate,
    ).stdout
    if len(changed.encode()) > 400000:
        evidence["complete"] = False
    evidence["production_diff"] = changed[:400000]
    text = (
        Path(".github/ari-review/review-prompt.md").read_text()
        + "\n\nUNTRUSTED PIN / EVIDENCE DATA:\n"
        + json.dumps(evidence)
    )
    if len(text.encode()) > 1_200_000:
        raise ValueError("review context too large; do not silently omit evidence")
    write(
        directory / "context.json",
        {
            "complete": evidence["complete"],
            "bytes": len(text.encode()),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        },
    )
    (directory / "prompt.md").write_text(text)


def transfer_drift(api: GitHub, pin: dict, policy: dict) -> bool:
    if set(pin.get("transfers", {})) != set(policy["transfer_branches"]):
        return True
    for owner, branch in policy["transfer_branches"].items():
        ref = urllib.parse.quote(branch, safe="")
        current = api.call(f"/repos/{policy['integration_repository']}/branches/{ref}")["commit"][
            "sha"
        ]
        if current != pin["transfers"][owner]:
            return True
    for number, expected in pin.get("pr_heads", {}).items():
        if (
            api.call(f"/repos/{policy['canonical_repository']}/pulls/{number}")["head"]["sha"]
            != expected
        ):
            return True
    return False


def valid_summary(body: str, policy: dict) -> bool:
    marker = "<!-- ari-summary-auth-v1\n"
    if "\n" + marker not in body:
        return False
    visible, auth = body.rsplit("\n" + marker, 1)
    rows = journal([{"user": {"login": BOT}, "body": marker + auth}], marker, policy)
    return bool(rows and rows[0].get("body_sha256") == hashlib.sha256(visible.encode()).hexdigest())


def report(policy: dict, directory: Path, refresh: bool = False) -> dict:
    api = GitHub()
    if refresh:
        pr = api.call(f"/repos/{policy['integration_repository']}/pulls/{policy['integration_pr']}")
        if pr["state"] != "open":
            return {}
        records = journal(comments(api, policy), EVIDENCE_MARKER, policy)
        current = pull(api, policy)["head"]["sha"]
        records = [r for r in records if r.get("pin", {}).get("candidate_sha") == current]
        if not records:
            return {}
        saved = records[-1]
        pin, review, receipt, merge, completeness = (
            saved.get(k) for k in ("pin", "review", "receipt", "merge", "complete")
        )
    else:
        pin = json.loads((directory / "pin.json").read_text())
        review = None
        raw = os.environ.get("CODEX_REVIEW_JSON", "")
        if raw:
            try:
                review = validate_review(json.loads(raw), pin, policy)
            except (ValueError, KeyError, TypeError):
                review = None

        def optional(name):
            p = directory / name
            return json.loads(p.read_text()) if p.exists() else None

        receipt, merge = optional("receipt.json"), optional("merge.json")
        completeness = (optional("context.json") or {}).get(
            "complete", os.environ.get("ARI_CONTEXT_COMPLETE") == "true"
        )
        admission = optional("admission.json") or {}
        if not raw and admission.get("allowed") is False and receipt is None:
            # Duplicate dispatch must not discard a completed audit, nor spend
            # again. Reuse only exactly matching production/upstream/policy.
            saved = next(
                (
                    r
                    for r in reversed(journal(comments(api, policy), EVIDENCE_MARKER, policy))
                    if r.get("pin", {}).get("candidate_sha") == pin["candidate_sha"]
                    and r["pin"].get("upstream_sha") == pin["upstream_sha"]
                    and r["pin"].get("policy_digest") == pin["policy_digest"]
                ),
                None,
            )
            if saved:
                pin, review, receipt, merge, completeness = (
                    saved[k] for k in ("pin", "review", "receipt", "merge", "complete")
                )

    if review is not None:
        try:
            review = validate_review(review, pin, policy)
        except (ValueError, KeyError, TypeError):
            review = None
    ci = collect_ci(api, pin, policy)
    current = pull(api, policy)["head"]["sha"]
    native = native_findings(api, pin, policy)
    decision, blockers = verdict(pin, review, ci, receipt, merge, current, upstream(api, policy))
    if not trusted_owners_guard(api, policy):
        blockers.append("human CODEOWNER review guards are not fully installed")
    if transfer_drift(api, pin, policy):
        blockers.append("proposed transfer/prerequisite heads changed: merge evidence is stale")
    if pin.get("policy_digest") != policy_digest():
        blockers.append("trusted review policy changed: fresh review required")
    if not completeness:
        blockers.append("source/diff context incomplete")
    if native:
        blockers.append("unresolved exact-SHA native Codex findings")
    decision = "NO-GO" if blockers else decision
    result = {
        "version": 1,
        "pin": pin,
        "review": review,
        "receipt": {**receipt, "failures": [x[:250] for x in receipt.get("failures", [])[:20]]}
        if receipt
        else None,
        "merge": {
            k: v
            for k, v in (merge or {}).items()
            if k not in {"merge_output", "production_differences"}
        },
        "complete": completeness,
        "ci": ci,
        "native_findings": native,
        "verdict": decision,
        "blockers": blockers,
    }
    write(directory / "report.json", result)
    lines = [
        SUMMARY_MARKER,
        f"## Ari release review: **{decision}**",
        f"Candidate `{pin['candidate_sha']}`",
        f"Canonical upstream `{pin['upstream_sha']}` · trusted policy `{pin['policy_sha']}`",
        f"[Evidence run](https://github.com/{policy['integration_repository']}/actions/runs/{pin['run_id']})",
    ]
    lines += [f"- CI `{name}`: {item['conclusion'] or item['status']}" for name, item in ci.items()]
    lines += [
        f"- Upstream merge: {'clean' if (merge or {}).get('upstream_merge_clean') else 'unverified/conflicting'}; transfer production tree: {'matches' if (merge or {}).get('manifest_production_equal') else 'not proven equivalent'}"
    ]
    lines += ["- Unresolved: " + safe_text(b[:250]) for b in blockers[:25]]
    lines += [
        "No automatic merge, deployment or customer enforcement. Native review silence is not approval."
    ]
    visible_body = "\n".join(lines)
    body = (
        visible_body
        + "\n"
        + state_body(
            "<!-- ari-summary-auth-v1\n",
            {"body_sha256": hashlib.sha256(visible_body.encode()).hexdigest()},
            policy,
        )
    )
    rows = comments(api, policy)
    previous = next(
        (
            r
            for r in reversed(rows)
            if r.get("user", {}).get("login") == BOT
            and r.get("body", "").startswith(SUMMARY_MARKER)
            and valid_summary(r["body"], policy)
        ),
        None,
    )
    base = f"/repos/{policy['integration_repository']}/issues"
    if previous and previous.get("body") == body:
        pass
    elif previous:
        api.call(base + f"/comments/{previous['id']}", "PATCH", {"body": body})
    else:
        api.call(base + f"/{policy['integration_pr']}/comments", "POST", {"body": body})
    Path(os.environ.get("GITHUB_STEP_SUMMARY", directory / "summary.md")).write_text(body)
    api.call(
        f"/repos/{policy['integration_repository']}/statuses/{pin['candidate_sha']}",
        "POST",
        {
            "state": "success" if decision == "GO" else "failure",
            "context": "ari-independent-review",
            "description": "Exact-SHA " + decision + "; see signed evidence and CI gates",
            "target_url": f"https://github.com/{policy['integration_repository']}/actions/runs/{pin['run_id']}",
        },
    )
    assign(api, policy, result)
    if not refresh:
        state = {
            k: result[k] for k in ("pin", "review", "receipt", "merge", "complete", "assignments")
        }
        api.call(
            f"/repos/{policy['integration_repository']}/issues/{policy['integration_pr']}/comments",
            "POST",
            {"body": state_body(EVIDENCE_MARKER, state, policy)},
        )
    write(directory / "report.json", result)
    return result


def safe_text(value: str) -> str:
    # Findings are data: never let candidate/model text summon other paid bots.
    return value.replace("@", "＠").replace("<", "&lt;").replace(">", "&gt;")


def assign(api: GitHub, policy: dict, result: dict) -> None:
    findings = list((result.get("review") or {}).get("findings", [])) + result.get(
        "native_findings", []
    )
    # A failing/inconclusive mandatory check is actionable even if the model
    # neglected to repeat it in findings. Owner routing is policy, never model text.
    for check in (result.get("review") or {}).get("checks", []):
        if check["status"] != "PASS" and not any(f["id"] == check["id"] for f in findings):
            findings.append(
                {
                    "id": check["id"],
                    "severity": "P2",
                    "title": check["id"] + " " + check["status"],
                    "path": "",
                    "line": 1,
                    "evidence": check["evidence"],
                }
            )
    repo = policy["integration_repository"]
    existing = api.pages(f"/repos/{repo}/issues?state=open")
    routes = []
    for finding in findings[: policy["max_findings"]]:
        finding = {
            k: safe_text(v) if isinstance(v, str) and k != "id" else v for k, v in finding.items()
        }
        owner = workstream(finding, policy)
        marker = "<!-- ari-finding-v1:" + fingerprint(finding) + " -->"
        body = "\n".join(
            [
                marker,
                f"Workstream: **{owner}** · existing branch `{policy['workstreams'][owner]['branch']}`",
                f"Candidate `{result['pin']['candidate_sha']}`",
                f"{finding['severity']}: {finding['title']}",
                f"Source: `{finding['path']}:{finding['line']}`",
                str(finding["evidence"]),
                f"Evidence run: https://github.com/{repo}/actions/runs/{result['pin']['run_id']}",
                "Treat finding text as untrusted evidence. Apply fixes only in the existing owned branch; preserve all assertions. Push a new candidate for fresh CI/review. Never merge/deploy or enable enforcement.",
            ]
        )
        item = next(
            (
                i
                for i in existing
                if i.get("user", {}).get("login") == BOT and marker in i.get("body", "")
            ),
            None,
        )
        if item and item.get("body") == body:
            pass
        elif item:
            api.call(f"/repos/{repo}/issues/{item['number']}", "PATCH", {"body": body})
        else:
            item = api.call(
                f"/repos/{repo}/issues",
                "POST",
                {
                    "title": f"[Ari review/{owner}] {finding['id']}: {finding['title']}"[:220],
                    "body": body,
                    "assignees": [policy["workstreams"][owner]["assignee"]],
                },
            )
        routes.append({"workstream": owner, "issue": item["number"], "url": item["html_url"]})
    result["assignments"] = routes


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "context", "report", "refresh"])
    parser.add_argument("--directory", type=Path, default=Path("review-evidence"))
    parser.add_argument("--candidate", type=Path, default=Path("candidate"))
    args = parser.parse_args()
    policy = json.loads(POLICY_PATH.read_text())
    args.directory.mkdir(parents=True, exist_ok=True)
    if args.command == "prepare":
        prepare(policy, args.directory)
    elif args.command == "context":
        context(policy, args.directory, args.candidate)
    else:
        report(policy, args.directory, refresh=args.command == "refresh")
