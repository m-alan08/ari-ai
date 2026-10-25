"""Trusted metadata, bounded GitHub I/O, validation and fail-closed verdicts."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

IDS = tuple([f"F{i}" for i in range(1, 11)] + [f"N{i}" for i in range(1, 8)])
SHA = re.compile(r"[0-9a-f]{40}\Z")
BOT = "github-actions[bot]"


def sha(value: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError("expected an immutable full commit SHA")
    return value


class GitHub:
    """No automatic retries of writes (including potentially ambiguous timeouts)."""

    def __init__(self, token: str | None = None):
        self.token = token if token is not None else os.environ["GH_TOKEN"]

    def call(self, path: str, method: str = "GET", body: Any = None) -> Any:
        if not path.startswith("/repos/") or ".." in path or "\n" in path:
            raise ValueError("invalid GitHub API path")
        req = urllib.request.Request(
            "https://api.github.com" + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError("oversized API response")
        return json.loads(raw) if raw else None

    def pages(self, path: str, key: str | None = None) -> list:
        out: list = []
        for page in range(1, 51):
            data = self.call(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            rows = data[key] if key else data
            out.extend(rows)
            if len(rows) < 100:
                return out
        raise ValueError("pagination limit: refuse incomplete evidence")


def comments(api: GitHub, policy: dict) -> list:
    return api.pages(
        f"/repos/{policy['integration_repository']}/issues/{policy['integration_pr']}/comments"
    )


def signed_payload(prefix: str, value: dict, policy: dict) -> dict:
    key = os.environ.get("ARI_REVIEW_STATE_KEY", "")
    if len(key) < 32:
        raise ValueError("protected review-state signing key is missing")
    payload = {
        **value,
        "_scope": policy["integration_repository"] + "#" + str(policy["integration_pr"]),
    }
    payload.pop("_signature", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(key.encode(), (prefix + canonical).encode(), hashlib.sha256).hexdigest()
    return {**payload, "_signature": signature}


def state_body(prefix: str, value: dict, policy: dict) -> str:
    # Keep durable state hidden; angle escaping prevents data ending the comment.
    encoded = (
        json.dumps(signed_payload(prefix, value, policy))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    return prefix + encoded + "\n-->"


def journal(rows: list, prefix: str, policy: dict) -> list[dict]:
    out = []
    for row in rows:
        if row.get("user", {}).get("login") != BOT:
            continue
        body = row.get("body", "")
        if body.startswith(prefix) and body.endswith("\n-->"):
            try:
                value = json.loads(body[len(prefix) : -4])
                expected = signed_payload(prefix, value, policy)
                if value.get("_scope") != expected["_scope"] or not hmac.compare_digest(
                    str(value.get("_signature", "")), expected["_signature"]
                ):
                    continue
                out.append(value)
            except (ValueError, TypeError):
                continue
    return out


def collect_ci(api: GitHub, pin: dict, policy: dict) -> dict:
    runs = api.pages(
        f"/repos/{policy['integration_repository']}/commits/{sha(pin['candidate_sha'])}/check-runs",
        "check_runs",
    )
    result = {}
    for name in policy["required_ci"]:
        same = [
            r
            for r in runs
            if r.get("head_sha") == pin["candidate_sha"]
            and r.get("name") == name
            and r.get("app", {}).get("slug") == "github-actions"
        ]
        latest = max(same, key=lambda r: r["id"], default=None)
        result[name] = {
            "status": latest.get("status") if latest else "missing",
            "conclusion": latest.get("conclusion") if latest else None,
            "url": latest.get("details_url") if latest else None,
        }
    return result


def native_findings(api: GitHub, pin: dict, policy: dict) -> list:
    base = f"/repos/{policy['integration_repository']}/pulls/{policy['integration_pr']}"
    reviews = api.pages(base + "/reviews")
    valid = {
        r["id"]
        for r in reviews
        if r.get("commit_id") == pin["candidate_sha"]
        and r.get("user", {}).get("login") == policy["native_reviewer"]
    }
    found = []
    for item in reviews:
        if item["id"] not in valid:
            continue
        match = re.search(r"\[P([0-3])\]", item.get("body") or "")
        if match:
            found.append(
                {
                    "id": f"NATIVE-REVIEW-{item['id']}",
                    "severity": "P" + match[1],
                    "title": item["body"][:180],
                    "path": "",
                    "line": 1,
                    "evidence": item.get("html_url", "Native review body"),
                }
            )
    for item in api.pages(base + "/comments"):
        if item.get("pull_request_review_id") not in valid:
            continue
        match = re.search(r"\[P([0-3])\]", item.get("body", ""))
        if match:
            found.append(
                {
                    "id": f"NATIVE-{item['id']}",
                    "severity": "P" + match[1],
                    "title": item["body"][:180],
                    "path": item.get("path", ""),
                    "line": item.get("line") or 1,
                    "evidence": item["html_url"],
                }
            )
    return found[: policy["max_findings"]]


def validate_review(data: dict, pin: dict, policy: dict) -> dict:
    if len(json.dumps(data).encode()) > 40000:
        raise ValueError("oversized structured review")
    if set(data) != {
        "version",
        "candidate_sha",
        "upstream_sha",
        "checks",
        "findings",
        "merge_equivalence",
        "summary",
    }:
        raise ValueError("invalid review envelope")
    if (
        data["version"] != 1
        or data["candidate_sha"] != pin["candidate_sha"]
        or data["upstream_sha"] != pin["upstream_sha"]
    ):
        raise ValueError("stale or foreign review")
    checks = data["checks"]
    if (
        not isinstance(checks, list)
        or len(checks) != len(IDS)
        or {x["id"] for x in checks} != set(IDS)
    ):
        raise ValueError("missing/duplicate required security findings")
    for item in checks:
        if set(item) != {"id", "status", "evidence"} or not item["evidence"]:
            raise ValueError("invalid or empty check evidence")
        if item["status"] not in {"PASS", "FAIL", "INCONCLUSIVE"} or not isinstance(
            item["evidence"], str
        ):
            raise ValueError("invalid security result")
    if data["merge_equivalence"] not in {"PASS", "FAIL", "INCONCLUSIVE"}:
        raise ValueError("invalid merge verdict")
    if not isinstance(data["summary"], str) or len(data["summary"]) > 2000:
        raise ValueError("oversized summary")
    if not isinstance(data["findings"], list) or len(data["findings"]) > policy["max_findings"]:
        raise ValueError("too many findings")
    for item in data["findings"]:
        if set(item) != {"id", "severity", "title", "path", "line", "evidence"}:
            raise ValueError("unexpected finding fields")
        path = item["path"]
        if (
            not isinstance(path, str)
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or "\n" in path
        ):
            raise ValueError("invalid finding path")
        if not re.fullmatch(r"(?:F[1-9][0-9]?|N[1-9][0-9]?|NEW-[A-Za-z0-9-]{1,50})", item["id"]):
            raise ValueError("invalid finding id")
        if (
            item["severity"] not in {"P0", "P1", "P2", "P3"}
            or type(item["line"]) is not int
            or item["line"] < 1
        ):
            raise ValueError("invalid finding severity/line")
        for key in ("title", "evidence"):
            if not isinstance(item[key], str) or len(item[key]) > 4000:
                raise ValueError("invalid finding text")
    return data


def workstream(finding: dict, policy: dict) -> str:
    for owner, config in policy["workstreams"].items():
        if finding["id"] in config["finding_ids"]:
            return owner
    path = finding["path"]
    if "redaction" in path or any(s in path for s in ("jobs.py", "execution.py", "worker.py")):
        return "reliability"
    if "onboarding" in path or "github_repos" in path:
        return "onboarding"
    if "investigation" in path:
        return "observability"
    return "integration"


def verdict(
    pin: dict,
    review: dict | None,
    ci: dict,
    receipt: dict | None,
    merge: dict | None,
    current_sha: str,
    upstream_sha: str,
) -> tuple[str, list[str]]:
    blockers = []
    if current_sha != pin["candidate_sha"]:
        blockers.append("candidate changed: fresh independent review required")
    if upstream_sha != pin["upstream_sha"]:
        blockers.append("upstream changed: merge comparison is stale")
    if not review:
        blockers.append("independent audit missing/invalid/setup required")
    else:
        blockers.extend(
            x["id"] + ": " + x["status"] for x in review["checks"] if x["status"] != "PASS"
        )
        blockers.extend(x["id"] + ": " + x["title"] for x in review["findings"])
        if review["merge_equivalence"] != "PASS":
            blockers.append("independent merge-equivalence audit did not pass")
    if not ci or any(
        x["status"] != "completed" or x["conclusion"] != "success" for x in ci.values()
    ):
        blockers.append("required same-SHA GitHub CI missing/pending/failed")
    if (
        not receipt
        or receipt.get("candidate_sha") != pin["candidate_sha"]
        or receipt.get("exit_code") != 0
        or receipt.get("tests", 0) < 20
        or receipt.get("skipped", 0)
    ):
        blockers.append("strict regression receipt missing/failed/skipped")
    if (
        not merge
        or merge.get("candidate_sha") != pin["candidate_sha"]
        or not merge.get("upstream_merge_clean")
        or not merge.get("manifest_production_equal")
    ):
        blockers.append("upstream merge / production-tree equivalence unverified")
    return ("NO-GO" if blockers else "GO"), blockers


def fingerprint(finding: dict) -> str:
    identity = (
        {"id": finding["id"]}
        if finding["id"] in IDS
        else {k: finding[k] for k in ("id", "path", "title")}
    )
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]


def utc_day() -> str:
    return datetime.now(UTC).date().isoformat()


def trusted_owners_guard(api: GitHub, policy: dict) -> bool:
    """Never release agent credentials before both controller guards are installed.

    Personal repositories lack organization-only actor push restrictions. Their
    required CODEOWNER reviews, together with environment branch restrictions,
    keep candidate/App-authored workflows away from protected credentials.
    """
    import base64
    import urllib.parse

    expected = {
        "/.github/ari-review/",
        "/.github/workflows/",
        "/.github/CODEOWNERS",
        "/scripts/ari_review/",
    }
    owner = "@" + policy["integration_repository"].split("/")[0]
    allowed = {owner, "@" + policy["canonical_repository"].split("/")[0]}
    for branch in ("main", "ci/upstream-main"):
        ref = urllib.parse.quote(branch, safe="")
        value = api.call(
            f"/repos/{policy['integration_repository']}/contents/.github/CODEOWNERS?ref={ref}"
        )
        text = base64.b64decode(value["content"]).decode()
        rows = [
            r.split() for r in text.splitlines() if r.strip() and not r.lstrip().startswith("#")
        ]
        tail = rows[-4:]
        if {r[0] for r in tail} != expected or any(
            owner not in r[1:] or not set(r[1:]) <= allowed for r in tail
        ):
            return False
    return True
