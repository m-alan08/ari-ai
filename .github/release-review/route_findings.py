#!/usr/bin/env python3
"""Route Codex audit findings to workstream owners and publish the GO/NO-GO report.

Reads the audit JSON emitted by openai/codex-action, deduplicates by SHA via a
marker comment on the integration PR, posts one comment per actionable finding
on the owning fork PR, optionally wakes the owner's Devin session via the
webhook automations listed in ARI_FIX_WEBHOOKS, and posts a single GO/NO-GO
report comment on the integration PR.

Environment:
  GITHUB_TOKEN        repo token (GITHUB_TOKEN from the workflow)
  REPO                owner/repo
  PR                  integration PR number
  CANDIDATE_SHA       exact commit reviewed (immutability anchor)
  BASE_SHA            upstream base SHA
  AUDIT_FILE          path to the codex-action output file (JSON)
  RUN_URL             workflow run URL
  CI_SUMMARY_FILE     file with one "<name>: <conclusion>" per CI check
  ARI_FIX_WEBHOOKS    JSON {webhook_key: {"url": ..., "secret": ...}} — optional;
                      absent/empty keys degrade to PR-comment routing only
  ROUTING_FILE        path to routing.json
  REPORT_FILE         path to write the coordination report markdown
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

MARKER = "ari-release-review"
ACTIONABLE = {"P0", "P1", "P2", "P3"}


def api(method, path, body=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['REPO']}{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def comment(issue_number, body):
    api("POST", f"/issues/{issue_number}/comments", {"body": body})


def already_reviewed(sha):
    for c in api("GET", f"/issues/{os.environ['PR']}/comments?per_page=100"):
        if MARKER in (c.get("body") or "") and sha in c["body"]:
            return True
    return False


def fire_devin_hook(webhooks, owner_key, finding, sha):
    hook = webhooks.get(owner_key)
    if not hook or not hook.get("url"):
        return "no webhook configured — PR comment only"
    payload = {
        "kind": "codex_finding_assignment",
        "owner_key": owner_key,
        "sha": sha,
        "pr": int(os.environ["PR"]),
        "finding": finding,
    }
    req = urllib.request.Request(
        hook["url"],
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload).encode(),
    )
    if hook.get("secret"):
        req.add_header("X-Webhook-Secret", hook["secret"])
    try:
        with urllib.request.urlopen(req) as r:
            return f"devin session woken (HTTP {r.status})"
    except Exception as exc:
        return f"webhook dispatch failed: {exc}"


def main():
    sha = os.environ["CANDIDATE_SHA"]
    routing = json.load(open(os.environ["ROUTING_FILE"]))["owners"]
    webhooks = json.loads(os.environ.get("ARI_FIX_WEBHOOKS") or "{}")

    if already_reviewed(sha):
        print(f"audit for {sha} already posted; dedup — nothing to do")
        return

    audit = json.load(open(os.environ["AUDIT_FILE"]))
    verdict = audit.get("verdict", "NO-GO")
    findings = audit.get("findings", [])
    summary_path = os.environ.get("CI_SUMMARY_FILE", "")
    if summary_path and os.path.exists(summary_path):
        ci_summary = open(summary_path).read().strip() or "(none)"
    else:
        # wait-ci cancelled/skipped (e.g. a newer push superseded this run) or
        # its artifact never uploaded — record it, never crash before reporting.
        ci_summary = "(ci summary unavailable — wait-ci did not complete)"

    routed = []
    for f in findings:
        sev = f.get("severity", "info")
        component = f.get("component") or "integration"
        owner = routing.get(component) or routing[routing.get("_default", "integration")]
        if component not in routing:
            component = "integration"
            owner = routing[component]
        line = f"{f.get('id', '?')} [{sev}] {f.get('title', '')} → {component}"
        if sev in ACTIONABLE and owner.get("owner_pr"):
            body = (
                f"<!-- {MARKER} {sha} {f.get('id')} -->\n"
                f"**Assigned finding {f.get('id')} ({sev})** on candidate `{sha}`\n\n"
                f"**{f.get('title', '')}**\n\n{f.get('detail', '')}\n\n"
                f"Files: {', '.join(f.get('paths') or []) or 'n/a'}\n"
                f"Run: {os.environ['RUN_URL']}\n\n"
                f"Owner: please fix on your workstream branch and report the fix SHA + tests. "
                f"The integration lead will recompose and this workflow will re-audit the new candidate SHA automatically."
            )
            comment(owner["owner_pr"], body)
            dispatch = fire_devin_hook(webhooks, component, f, sha)
            line += f" — commented PR #{owner['owner_pr']}; {dispatch}"
        elif sev in ACTIONABLE:
            line += " — actionable but no owner PR (needs manual relay)"
        else:
            line += " — informational, no owner routing"
        routed.append(line)

    unresolved = [entry for entry in routed if "actionable but no owner PR" in entry]
    report = [
        f"<!-- {MARKER} {sha} -->",
        f"## Release review — `{sha}`",
        f"",
        f"**Verdict: {verdict}** (independent Codex audit, exact-SHA)",
        f"",
        f"- Base: `{os.environ['BASE_SHA']}`",
        f"- Run: {os.environ['RUN_URL']}",
        f"- Summary: {audit.get('summary', '')}",
        f"",
        f"### CI",
        "```",
        ci_summary,
        "```",
        f"",
        f"### Findings routing",
        *([f"- {l}" for l in routed] if routed else ["- no findings"]),
        f"",
        f"### Unresolved",
        *([f"- {u}" for u in unresolved] if unresolved else ["- none"]),
        f"",
        f"Merge readiness: upstream stack `transfer/01-pr412` → `transfer/release-validation`"
        f" composes to this tree (manifest test in strict-regressions job).",
    ]
    comment(os.environ["PR"], "\n".join(report))

    report_file = os.environ.get("REPORT_FILE")
    if report_file:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        os.makedirs(os.path.dirname(report_file), exist_ok=True)
        with open(report_file, "w") as fh:
            fh.write(f"# Release review {sha}\n\n" + "\n".join(report[1:]) + "\n")

    print(verdict)
    if verdict != "GO":
        print("NO-GO: see routed findings above", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
