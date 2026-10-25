"""Monotonic budget ledger on a protected, fast-forward-only Git branch.

Comment deletion cannot reset spend. Every signed snapshot binds its sole Git
parent. Copying an older signed tree into a new commit fails that binding; a
rollback/delete is rejected by branch protection. A race loses its non-forced
ref update and fails before any paid side effect. No write retries.
"""

from __future__ import annotations

import base64
import json
import os

from scripts.ari_review.common import GitHub, journal, signed_payload

BRANCH = "automation/ari-review-ledger"
PREFIX = "ari-monotonic-ledger-v1\n"


class Ledger:
    def __init__(self, api: GitHub, policy: dict):
        self.api, self.policy = api, policy
        self.root = "/repos/" + policy["integration_repository"]

    def load(self) -> tuple[str, dict]:
        head = self.api.call(self.root + "/git/ref/heads/" + BRANCH)["object"]["sha"]
        commit = self.api.call(self.root + "/git/commits/" + head)
        tree = self.api.call(self.root + "/git/trees/" + commit["tree"]["sha"])["tree"]
        if len(tree) != 1 or tree[0]["path"] != "ledger.json" or tree[0]["type"] != "blob":
            raise ValueError("ledger tree changed; refuse budget reset")
        blob = self.api.call(self.root + "/git/blobs/" + tree[0]["sha"])
        raw = base64.b64decode(blob["content"]).decode()
        # Reuse the same strict HMAC validator with a synthetic authenticated row.
        rows = journal(
            [{"user": {"login": "github-actions[bot]"}, "body": PREFIX + raw + "\n-->"}],
            PREFIX,
            self.policy,
        )
        if (
            not rows
            or len(commit["parents"]) != 1
            or rows[0]["parent"] != commit["parents"][0]["sha"]
        ):
            raise ValueError("invalid/replayed ledger signature or parent")
        genesis = os.environ.get("ARI_REVIEW_LEDGER_GENESIS", "")
        if len(genesis) != 40:
            raise ValueError("protected ledger genesis anchor missing")
        comparison = self.api.call(self.root + "/compare/" + genesis + "..." + head)
        if (
            comparison["status"] not in {"identical", "ahead"}
            or comparison["merge_base_commit"]["sha"] != genesis
        ):
            raise ValueError("ledger is not descended from the protected genesis")
        return head, rows[0]

    def reserve(self, kind: str, entry: dict) -> dict:
        parent, state = self.load()
        if kind not in {"cycles", "reviews", "devin"}:
            raise ValueError("unknown ledger kind")
        records = state[kind]
        if kind == "devin":
            if any(
                r["candidate_sha"] == entry["candidate_sha"]
                and r["owner"] == entry["owner"]
                and r["phase"] == entry["phase"]
                for r in records
            ):
                raise ValueError("Devin handoff already reserved")
            if (
                len(records) >= self.policy["max_fix_messages_per_pr"]
                or sum(r["day"] == entry["day"] for r in records)
                >= self.policy["max_fix_messages_per_day"]
            ):
                raise ValueError("Devin invocation cap reached")
            rounds = {r["candidate_sha"] for r in records}
            if (
                entry["candidate_sha"] not in rounds
                and len(rounds) >= self.policy["max_fix_rounds_per_pr"]
            ):
                raise ValueError("automatic fix rounds exhausted")
        else:
            if any(r["candidate_sha"] == entry["candidate_sha"] for r in records):
                raise ValueError("SHA already reserved; no automatic retries")
            daily = [r for r in records if r["day"] == entry["day"]]
            if len(daily) >= self.policy["max_reviews_per_day"]:
                raise ValueError("daily candidate cap reached")
            if kind == "reviews":
                from decimal import Decimal

                used = sum(Decimal(r["reserved_usd"]) for r in daily)
                if used + Decimal(entry["reserved_usd"]) > Decimal(self.policy["max_usd_per_day"]):
                    raise ValueError("daily USD cap reached")
        if sum(len(state[k]) for k in ("cycles", "reviews", "devin")) >= 10000:
            raise ValueError("ledger capacity reached; owner migration required")
        state = {
            **state,
            kind: [*records, entry],
            "parent": parent,
            "sequence": state["sequence"] + 1,
        }
        state = signed_payload(PREFIX, state, self.policy)
        tree = self.api.call(
            self.root + "/git/trees",
            "POST",
            {
                "tree": [
                    {
                        "path": "ledger.json",
                        "mode": "100644",
                        "type": "blob",
                        "content": json.dumps(state),
                    }
                ]
            },
        )
        commit = self.api.call(
            self.root + "/git/commits",
            "POST",
            {
                "message": "chore(review-state): reserve bounded " + kind,
                "tree": tree["sha"],
                "parents": [parent],
            },
        )
        # force=false is deliberate. A concurrent reservation invalidates this
        # update rather than allowing both callers to spend against old state.
        self.api.call(
            self.root + "/git/refs/heads/" + BRANCH, "PATCH", {"sha": commit["sha"], "force": False}
        )
        return state
