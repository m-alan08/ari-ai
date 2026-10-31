"""#693 x #694 integration: a review POST already on the wire holds the disconnect write lease.

#693 alone re-checked the connection under the repository row lock inside its effect
reservation and released it before the POST, so a disconnect could return 200 while the
review request was still in flight (its scorecard's accepted residual). With #694's
`github_write` lease around the reservation and the POST, disconnect waits for the POST to
finish, and a review never starts after disconnect has returned 200.
"""

# ruff: noqa: F401, F811

from __future__ import annotations

import threading

from test_auto_commits import automatic, seed_pr_assessment
from test_disconnect_write_token_ordering import Timeline, _disconnect_in_background

from backend import worker


def test_disconnect_waits_for_an_in_flight_pr_review_post(automatic, monkeypatch) -> None:
    alice, bob, ids, _ = automatic
    seed_pr_assessment(ids, bob)
    timeline = Timeline()
    entered, release = threading.Event(), threading.Event()

    class GitHub:
        def post_pull_request_review(self, *args):
            entered.set()
            assert release.wait(15), "test never released the review POST"
            timeline.add("review_post")

    monkeypatch.setattr("backend.auto_commits.GitHubApp", GitHub)
    writer = threading.Thread(
        target=worker.run_one, args=("review-writer", ["post_pr_review"]), daemon=True
    )
    writer.start()
    assert entered.wait(10), "the review job never reached the POST"

    disconnect = _disconnect_in_background(alice, ids["repo"], timeline)
    disconnect.join(1.5)
    try:
        assert disconnect.is_alive(), (
            f"disconnect returned while a review POST was in flight: {timeline.events}"
        )
    finally:
        release.set()
        writer.join(15)
        disconnect.join(15)

    events = timeline.events
    assert events.index("review_post") < events.index("disconnect:200"), events
    timeline.assert_no_provider_write_after_disconnect_ack()
