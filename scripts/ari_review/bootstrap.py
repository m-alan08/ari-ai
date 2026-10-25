"""One-time fork admin setup. Creates no paid credentials and enables no agent.

Idempotently provision trusted-branch secret environments and a signed budget
ledger. Never reset an existing ledger/key and never weaken existing protection.
Use --apply explicitly; without it this is a read-only setup inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
from pathlib import Path

from scripts.ari_review.common import signed_payload
from scripts.ari_review.ledger import BRANCH, PREFIX


class Admin:
    def call(self, path: str, method="GET", body=None, missing=False):
        command = ["gh", "api", path.removeprefix("/"), "--method", method]
        if body is not None:
            command += ["--input", "-"]
        run = subprocess.run(
            command,
            input=json.dumps(body) if body is not None else None,
            text=True,
            capture_output=True,
        )
        if run.returncode:
            if missing and "(HTTP 404)" in run.stderr:
                return None
            raise RuntimeError(run.stderr.strip() + " " + run.stdout[:2500])
        return json.loads(run.stdout) if run.stdout.strip() else None

    def secret(self, repo: str, environment: str, name: str, value: str):
        subprocess.run(
            ["gh", "secret", "set", name, "--repo", repo, "--env", environment],
            input=value,
            text=True,
            check=True,
        )


def protect(api: Admin, repo: str, branch: str, ledger=False, apply=False) -> None:
    import urllib.parse

    path = f"/repos/{repo}/branches/{urllib.parse.quote(branch, safe='')}/protection"
    current = api.call(path, missing=True)
    personal = api.call(f"/repos/{repo}")["owner"]["type"] == "User"
    if current:
        if current.get("allow_force_pushes", {}).get("enabled") or current.get(
            "allow_deletions", {}
        ).get("enabled"):
            raise ValueError("existing protection allows rollback/deletion; owner must tighten it")
        if ledger:
            if not current.get("enforce_admins", {}).get("enabled") or not current.get(
                "required_linear_history", {}
            ).get("enabled"):
                raise ValueError(
                    "existing ledger protection is insufficient; never overwrite silently"
                )
        else:
            review = current.get("required_pull_request_reviews") or {}
            if review.get("required_approving_review_count", 0) < 1 or not review.get(
                "require_code_owner_reviews"
            ):
                raise ValueError("trusted controller branch needs owner-reviewed PRs")
        if not ledger and not personal:
            restrictions = current.get("restrictions") or {}
            owner = repo.split("/")[0]
            users = {u["login"] for u in restrictions.get("users", [])}
            if users != {owner} or restrictions.get("apps") or restrictions.get("teams"):
                if not apply:
                    print("MISSING owner-only controller push restriction:", branch)
                    return
                if current.get("required_status_checks") is not None:
                    raise ValueError(
                        "custom existing checks require an owner-reviewed restriction migration"
                    )
                # Tighten only our new, otherwise unrestricted controller rule.
                # Do not silently rewrite custom existing branch protections.
                api.call(
                    path,
                    "PUT",
                    {
                        "required_status_checks": None,
                        "enforce_admins": current.get("enforce_admins", {}).get("enabled", False),
                        "required_pull_request_reviews": {
                            k: v
                            for k, v in review.items()
                            if k
                            in {
                                "dismiss_stale_reviews",
                                "require_code_owner_reviews",
                                "required_approving_review_count",
                                "require_last_push_approval",
                            }
                        },
                        "restrictions": {"users": [owner], "teams": [], "apps": []},
                        "required_linear_history": current.get("required_linear_history", {}).get(
                            "enabled", False
                        ),
                        "required_conversation_resolution": current.get(
                            "required_conversation_resolution", {}
                        ).get("enabled", False),
                        "allow_force_pushes": False,
                        "allow_deletions": False,
                    },
                )
        return
    if not apply:
        print("MISSING protected branch:", branch)
        return
    api.call(
        path,
        "PUT",
        {
            "required_status_checks": None,
            "enforce_admins": ledger,
            "required_pull_request_reviews": None
            if ledger
            else {
                "dismiss_stale_reviews": True,
                "require_code_owner_reviews": True,
                "required_approving_review_count": 1,
                "require_last_push_approval": True,
            },
            "restrictions": None
            if ledger or personal
            else {"users": [repo.split("/")[0]], "teams": [], "apps": []},
            "required_linear_history": ledger,
            "allow_force_pushes": False,
            "allow_deletions": False,
        },
    )


def bootstrap(apply: bool, recovery: Path):
    policy = json.loads(Path(".github/ari-review/policy.json").read_text())
    repo = policy["integration_repository"]
    api = Admin()
    for branch in ("main", "ci/upstream-main"):
        protect(api, repo, branch, apply=apply)
    for name, branches in {
        "ari-review-control": ["main", "ci/upstream-main"],
        "ari-independent-review": ["main", "ci/upstream-main"],
        "ari-devin-handoff": ["main"],
    }.items():
        path = f"/repos/{repo}/environments/{name}"
        exists = api.call(path, missing=True)
        if exists:
            deployment = exists.get("deployment_branch_policy") or {}
            rules = api.call(path + "/deployment-branch-policies")["branch_policies"]
            if not deployment.get("custom_branch_policies") or {
                (r["name"], r.get("type", "branch")) for r in rules
            } != {(b, "branch") for b in branches}:
                raise ValueError(
                    "existing review environment is not restricted to exact trusted branches: "
                    + name
                )
        elif apply:
            api.call(
                path,
                "PUT",
                {
                    "deployment_branch_policy": {
                        "protected_branches": False,
                        "custom_branch_policies": True,
                    }
                },
            )
            for branch in branches:
                api.call(
                    path + "/deployment-branch-policies", "POST", {"name": branch, "type": "branch"}
                )
        else:
            print("MISSING trusted secret environment:", name)
    ref = api.call(f"/repos/{repo}/git/ref/heads/{BRANCH}", missing=True)
    if not ref:
        if not apply:
            print("MISSING monotonic budget ledger and internal signing key")
            return
        if recovery.exists():
            saved = json.loads(recovery.read_text())
        else:
            saved = {"key": secrets.token_urlsafe(48)}
            fd = os.open(recovery, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump(saved, handle)
        os.environ["ARI_REVIEW_STATE_KEY"] = saved["key"]
        parent = api.call(f"/repos/{repo}/commits/main")["sha"]
        data = signed_payload(
            PREFIX,
            {
                "version": 1,
                "parent": parent,
                "sequence": 0,
                "cycles": [],
                "reviews": [],
                "devin": [],
            },
            policy,
        )
        tree = api.call(
            f"/repos/{repo}/git/trees",
            "POST",
            {
                "tree": [
                    {
                        "path": "ledger.json",
                        "mode": "100644",
                        "type": "blob",
                        "content": json.dumps(data),
                    }
                ]
            },
        )
        commit = api.call(
            f"/repos/{repo}/git/commits",
            "POST",
            {
                "message": "chore(review-state): initialize protected review ledger",
                "tree": tree["sha"],
                "parents": [parent],
            },
        )
        api.call(
            f"/repos/{repo}/git/refs", "POST", {"ref": "refs/heads/" + BRANCH, "sha": commit["sha"]}
        )
        saved["genesis"] = commit["sha"]
        recovery.write_text(json.dumps(saved))
        ref = {"object": {"sha": commit["sha"]}}
    protect(api, repo, BRANCH, ledger=True, apply=apply)
    if apply and recovery.exists():
        saved = json.loads(recovery.read_text())
        for name in ("ari-review-control", "ari-devin-handoff"):
            api.secret(repo, name, "ARI_REVIEW_STATE_KEY", saved["key"])
            api.secret(repo, name, "ARI_REVIEW_LEDGER_GENESIS", saved["genesis"])
        recovery.unlink()
    print(
        "Review controls inspected. Existing ledgers/keys are never reset. OpenAI/Devin API activation remains OFF."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--recovery",
        type=Path,
        default=Path("/private/tmp/ari-review-control-bootstrap.json")
        if os.name == "posix" and Path("/private/tmp").exists()
        else Path("/tmp/ari-review-control-bootstrap.json"),
    )
    args = parser.parse_args()
    bootstrap(args.apply, args.recovery)
