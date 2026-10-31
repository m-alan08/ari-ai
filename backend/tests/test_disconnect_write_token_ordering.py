"""Codex review (2026-10-29): write-token acquisition must sit inside the disconnect boundary.

Delivery and recipe proposal re-checked the connection, *released* nothing, and then minted
a GitHub write token. A disconnect could commit and return 200 in between, after which the
job still asked GitHub for a new write token. The write lease (`connection_guard.github_write`)
now surrounds the mint, so for every write-capable provider request:

* a request already holding the lease completes before disconnect acknowledges success (or
  disconnect returns its documented 409 timeout), and
* once disconnect has returned 200, no new write token, push, pull request or review starts.

Every test runs the job and the disconnect on different threads against the real local
Postgres, so the ordering comes from the advisory lease, not from same-thread sequencing.
"""

# ruff: noqa: F401, F811

from __future__ import annotations

import functools
import threading
from collections.abc import Callable

import httpx
import pytest
import sqlalchemy as sa
from test_auto_commits import automatic, seed_pr_assessment
from test_auto_delivery import FakeApp, _actions, reference, wired
from test_build_recipes import github

from backend import worker
from backend.db import jobs, transaction


class Timeline:
    """Thread-safe record of provider requests and disconnect acknowledgements."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[str] = []

    def add(self, event: str) -> None:
        with self._lock:
            self.events.append(event)

    def assert_no_provider_write_after_disconnect_ack(self) -> None:
        events = list(self.events)
        assert "disconnect:200" in events, events
        acked = events.index("disconnect:200")
        late = [e for e in events[acked:] if not e.startswith("disconnect")]
        assert late == [], f"provider write access after disconnect returned 200: {events}"


def _disconnect_in_background(client, repo_id: str, timeline: Timeline) -> threading.Thread:
    def run() -> None:
        timeline.add(
            f"disconnect:{client.post(f'/api/v1/repositories/{repo_id}/disconnect').status_code}"
        )

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _disconnect_after_first_passing_check(
    monkeypatch, client, repo_id: str, timeline: Timeline
) -> list[threading.Thread]:
    """The owner disconnects right after the job's first passing connection check.

    The disconnect runs on its own thread; the job thread waits briefly for it. If the
    check ran outside the write lease the disconnect commits and returns 200 at once."""
    from backend import connection_guard

    real = connection_guard.connection_problem
    started: list[threading.Thread] = []

    def check(repo: str, installation_id: int) -> str | None:
        reason = real(repo, installation_id)
        if reason is None and not started:
            started.append(_disconnect_in_background(client, repo_id, timeline))
            started[0].join(1.5)
        return reason

    monkeypatch.setattr(connection_guard, "connection_problem", check)
    return started


def _recording_delivery_app(
    monkeypatch, timeline: Timeline, before_mint: Callable[[], None] | None = None
):
    class RecordingApp(FakeApp):
        def installation_token(self, installation_id, *, write=False, repository=None):
            if write and before_mint is not None:
                before_mint()
            token = super().installation_token(installation_id, write=write, repository=repository)
            if write:
                timeline.add("write_token")
            return token

        def open_pull_request(self, installation_id, full_name, **kw):
            timeline.add("pull_request")
            return super().open_pull_request(installation_id, full_name, **kw)

    monkeypatch.setattr("backend.github.GitHubApp", RecordingApp)

    from backend import delivery

    real_push = delivery.push_patch

    def push(*a, **k):
        timeline.add("push")
        return real_push(*a, **k)

    monkeypatch.setattr(delivery, "push_patch", push)


# --- Ordering 1: once disconnect returns 200, nothing new starts -------------------------------


@pytest.mark.parametrize("mode", ["auto", "pull_request"])
def test_delivery_never_mints_a_write_token_after_disconnect_returned_200(
    wired, monkeypatch, mode
) -> None:
    alice, ids, _ = wired(mode)
    timeline = Timeline()
    _recording_delivery_app(monkeypatch, timeline)
    started = _disconnect_after_first_passing_check(monkeypatch, alice, ids["repo"], timeline)

    worker.drain(kinds=["deliver_verified_patch"])
    assert started, "delivery never checked the connection"
    started[0].join(15)

    timeline.assert_no_provider_write_after_disconnect_ack()
    assert FakeApp.calls == []
    assert "delivery.pushed" not in _actions()
    assert "delivery.pr_opened" not in _actions()


def test_retried_delivery_after_disconnect_requests_no_new_write_token(wired, monkeypatch) -> None:
    alice, ids, _ = wired("pull_request")
    timeline = Timeline()
    _recording_delivery_app(monkeypatch, timeline)
    flaky = {"left": 1}
    real_open = FakeApp.open_pull_request

    def open_once_flaky(self, installation_id, full_name, **kw):
        if flaky["left"]:
            flaky["left"] -= 1
            raise httpx.ConnectError("GitHub connection reset")
        return real_open(self, installation_id, full_name, **kw)

    monkeypatch.setattr(FakeApp, "open_pull_request", open_once_flaky)
    worker.drain(kinds=["deliver_verified_patch"])
    with transaction() as conn:
        status = conn.execute(
            sa.select(jobs.c.status).where(jobs.c.kind == "deliver_verified_patch")
        ).scalar_one()
    assert status == "queued", "the transient PR failure should leave a retry queued"

    disconnect = _disconnect_in_background(alice, ids["repo"], timeline)
    disconnect.join(15)
    with transaction() as conn:
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.kind == "deliver_verified_patch")
            .values(run_after=sa.func.now() - sa.text("interval '1 second'"))
        )
    worker.drain(kinds=["deliver_verified_patch"])

    timeline.assert_no_provider_write_after_disconnect_ack()
    assert [c for c in FakeApp.calls if c[0] == "pr"] == []
    assert "delivery.pr_opened" not in _actions()


def test_recipe_proposal_never_mints_a_write_token_after_disconnect_returned_200(
    client_factory, github, monkeypatch
) -> None:
    from test_build_recipes import SHOP, _audit, _connect
    from test_build_recipes import FakeApp as RecipeApp

    bare = github(SHOP)
    alice = client_factory("alice")
    repo_id = _connect(alice)
    timeline = Timeline()

    class RecordingRecipeApp(RecipeApp):
        def installation_token(self, installation_id, *, write=False, repository=None):
            token = super().installation_token(installation_id, write=write, repository=repository)
            if write:
                timeline.add("write_token")
            return token

        def open_pull_request(self, installation_id, full_name, **kw):
            timeline.add("pull_request")
            return super().open_pull_request(installation_id, full_name, **kw)

    monkeypatch.setattr("backend.github.GitHubApp", RecordingRecipeApp)
    started = _disconnect_after_first_passing_check(monkeypatch, alice, repo_id, timeline)

    worker.drain(kinds=["propose_build_recipe"])
    assert started, "recipe proposal never checked the connection"
    started[0].join(15)

    from test_security_audit_disconnect_writes import _recipe_branch

    timeline.assert_no_provider_write_after_disconnect_ack()
    assert RecipeApp.prs == []
    assert _recipe_branch(bare) == ""
    assert _audit("recipe.pr_opened") == []


def test_pr_review_never_posts_after_disconnect_returned_200(automatic, monkeypatch) -> None:
    alice, bob, ids, _ = automatic
    seed_pr_assessment(ids, bob)
    timeline = Timeline()

    class GitHub:
        def post_pull_request_review(self, *args):
            # The real client mints its write token inside this call.
            timeline.add("write_token+review")

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    started = _disconnect_after_first_passing_check(monkeypatch, alice, ids["repo"], timeline)

    worker.drain(kinds=["post_pr_review"])
    assert started, "the review job never checked the connection"
    started[0].join(15)
    timeline.assert_no_provider_write_after_disconnect_ack()


# --- Ordering 2: a request already holding the lease finishes before disconnect acks -----------


def _blocked_mint(monkeypatch, timeline: Timeline):
    entered, release = threading.Event(), threading.Event()

    def block() -> None:
        entered.set()
        assert release.wait(15), "test never released the token request"

    _recording_delivery_app(monkeypatch, timeline, before_mint=block)
    return entered, release


def test_disconnect_waits_for_an_in_flight_write_token_request(wired, monkeypatch) -> None:
    alice, ids, _ = wired("pull_request")
    timeline = Timeline()
    entered, release = _blocked_mint(monkeypatch, timeline)
    writer = threading.Thread(
        target=worker.run_one, args=("mint-writer", ["deliver_verified_patch"]), daemon=True
    )
    writer.start()
    assert entered.wait(10), "delivery never requested a write token"

    disconnect = _disconnect_in_background(alice, ids["repo"], timeline)
    disconnect.join(1.5)
    try:
        assert disconnect.is_alive(), (
            f"disconnect returned while a write-token request was in flight: {timeline.events}"
        )
    finally:
        release.set()
        writer.join(15)
        disconnect.join(15)

    events = timeline.events
    assert events.index("write_token") < events.index("disconnect:200"), events
    timeline.assert_no_provider_write_after_disconnect_ack()


def test_disconnect_times_out_with_409_while_a_write_token_request_is_stuck(
    wired, monkeypatch
) -> None:
    from backend.api import routes
    from backend.connection_guard import hold_github_writes

    monkeypatch.setattr(
        routes, "hold_github_writes", functools.partial(hold_github_writes, timeout_s=1)
    )
    alice, ids, _ = wired("pull_request")
    timeline = Timeline()
    entered, release = _blocked_mint(monkeypatch, timeline)
    writer = threading.Thread(
        target=worker.run_one, args=("mint-writer", ["deliver_verified_patch"]), daemon=True
    )
    writer.start()
    assert entered.wait(10), "delivery never requested a write token"
    try:
        disconnect = _disconnect_in_background(alice, ids["repo"], timeline)
        disconnect.join(10)
        assert timeline.events == ["disconnect:409"], timeline.events
    finally:
        release.set()
        writer.join(15)
    # Disconnect did not happen, so the delivery that held the lease legitimately finishes.
    assert "delivery.pr_opened" in _actions()
