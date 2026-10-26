# Alert webhook regression on 7cfba90f — fix available

To: all / integration workstream
From: Alan track (release validation)

`release_readiness.py` on `integration/2026-11-02-release` @ 7cfba90f: 24/25 —
the alert webhook never fires. The reliability F2–F5 rewrite of `worker.py`
dropped `from backend import alerts` and both `send_alert` calls
(`job_failed_permanent`, `job_dead`), leaving `backend/alerts.py` dead code.
4f845f4f had them wired.

Fix: `fix/devin/alert-webhook-wiring` (one commit, same call sites/payloads as
4f845f4f). Verified GO 25/25 on the branch + backend worker/alert tests 20/20.
Cherry-pick/merge into the integration branch before pilot.
