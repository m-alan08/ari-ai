"""Deleting/spoofing comments must not reset budgets or forge independent GO."""

import base64
import json

import pytest

from scripts.ari_review.common import journal, signed_payload, state_body
from scripts.ari_review.cycle import valid_summary
from scripts.ari_review.ledger import PREFIX, Ledger

POLICY = json.loads(open(".github/ari-review/policy.json").read())


class API:
    def __init__(self, state):
        self.state = state
        self.parent = state["parent"]
        self.calls = []

    def call(self, path, method="GET", body=None):
        self.calls.append((path, method, body))
        if method == "PATCH":
            assert body["force"] is False
            return {}
        if method == "POST":
            return {"sha": "e" * 40}
        if "/git/ref/" in path:
            return {"object": {"sha": "c" * 40}}
        if "/git/commits/" in path:
            return {"tree": {"sha": "d" * 40}, "parents": [{"sha": self.parent}]}
        if "/git/trees/" in path:
            return {"tree": [{"path": "ledger.json", "type": "blob", "sha": "f" * 40}]}
        if "/git/blobs/" in path:
            return {"content": base64.b64encode(json.dumps(self.state).encode()).decode()}
        if "/compare/" in path:
            return {"status": "ahead", "merge_base_commit": {"sha": "a" * 40}}
        raise AssertionError(path)


def state():
    return signed_payload(
        PREFIX,
        {"version": 1, "parent": "b" * 40, "sequence": 0, "cycles": [], "reviews": [], "devin": []},
        POLICY,
    )


def test_signed_git_parent_prevents_old_snapshot_replay(monkeypatch):
    monkeypatch.setenv("ARI_REVIEW_LEDGER_GENESIS", "a" * 40)
    api = API(state())
    api.parent = "9" * 40  # Attacker copied a valid old signed tree into a newer commit.
    with pytest.raises(ValueError, match="parent"):
        Ledger(api, POLICY).load()


def test_unsigned_bot_comment_cannot_forge_review():
    prefix = "<!-- ari-review-evidence-v1\n"
    fake = {
        "user": {"login": "github-actions[bot]"},
        "body": prefix + json.dumps({"verdict": "GO"}) + "\n-->",
    }
    assert journal([fake], prefix, POLICY) == []


def test_signed_state_cannot_cross_repository_or_pr():
    prefix = "<!-- ari-review-evidence-v1\n"
    row = {
        "user": {"login": "github-actions[bot]"},
        "body": state_body(prefix, {"candidate_sha": "a" * 40}, POLICY),
    }
    assert journal([row], prefix, POLICY)
    assert not journal([row], prefix, {**POLICY, "integration_pr": 9})


def test_comment_deletion_does_not_reset_protected_budget(monkeypatch):
    monkeypatch.setenv("ARI_REVIEW_LEDGER_GENESIS", "a" * 40)
    data = state()
    data["reviews"] = [{"candidate_sha": "a" * 40, "day": "2026-10-25", "reserved_usd": "5.00"}]
    data = signed_payload(PREFIX, data, POLICY)
    with pytest.raises(ValueError, match="already reserved"):
        Ledger(API(data), POLICY).reserve(
            "reviews", {"candidate_sha": "a" * 40, "day": "2026-10-25", "reserved_usd": "5.00"}
        )


def test_reservation_updates_ref_without_force_before_any_paid_side_effect(monkeypatch):
    monkeypatch.setenv("ARI_REVIEW_LEDGER_GENESIS", "a" * 40)
    api = API(state())
    Ledger(api, POLICY).reserve(
        "reviews", {"candidate_sha": "a" * 40, "day": "2026-10-25", "reserved_usd": "5.00"}
    )
    assert api.calls[-1][1] == "PATCH"
    assert api.calls[-1][2]["force"] is False


def test_summary_text_cannot_be_changed_to_go_with_copied_signature():
    visible = "<!-- ari-release-summary-v1 -->\nNO-GO"
    import hashlib

    body = (
        visible
        + "\n"
        + state_body(
            "<!-- ari-summary-auth-v1\n",
            {"body_sha256": hashlib.sha256(visible.encode()).hexdigest()},
            POLICY,
        )
    )
    assert valid_summary(body, POLICY)
    assert not valid_summary(body.replace("\nNO-GO", "\nGO"), POLICY)


def test_same_sha_cannot_retry_on_another_day(monkeypatch):
    monkeypatch.setenv("ARI_REVIEW_LEDGER_GENESIS", "a" * 40)
    data = state()
    data["reviews"] = [{"candidate_sha": "a" * 40, "day": "2026-10-24", "reserved_usd": "5.00"}]
    with pytest.raises(ValueError, match="already reserved"):
        Ledger(API(signed_payload(PREFIX, data, POLICY)), POLICY).reserve(
            "reviews", {"candidate_sha": "a" * 40, "day": "2026-10-25", "reserved_usd": "5.00"}
        )


def test_daily_cap_is_enforced_by_protected_ledger(monkeypatch):
    monkeypatch.setenv("ARI_REVIEW_LEDGER_GENESIS", "a" * 40)
    data = state()
    data["reviews"] = [
        {"candidate_sha": c * 40, "day": "2026-10-25", "reserved_usd": "5.00"} for c in "cde"
    ]
    with pytest.raises(ValueError, match="daily candidate cap"):
        Ledger(API(signed_payload(PREFIX, data, POLICY)), POLICY).reserve(
            "reviews", {"candidate_sha": "a" * 40, "day": "2026-10-25", "reserved_usd": "5.00"}
        )
