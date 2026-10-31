import importlib.util
import io
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "uptime_watch.py"
SPEC = importlib.util.spec_from_file_location("uptime_watch", SCRIPT_PATH)
uptime_watch = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = uptime_watch
SPEC.loader.exec_module(uptime_watch)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(body):
    return lambda request, timeout: _Resp(body)


def opener_raising(error):
    def _open(request, timeout):
        raise error

    return _open


class FakeClient:
    def __init__(self, issues=None):
        self.issues = issues or []
        self.calls = []

    def get(self, path):
        self.calls.append(("GET", path, None))
        return self.issues

    def post(self, path, payload):
        self.calls.append(("POST", path, payload))

    def patch(self, path, payload):
        self.calls.append(("PATCH", path, payload))


LIVE = [
    {"name": "ari-ai", "deployment": {"status": "SUCCESS"}},
    {"name": "Postgres", "deployment": {"status": "SUCCESS"}},
]


class HealthTests(unittest.TestCase):
    def test_ok(self):
        self.assertIsNone(
            uptime_watch.check_health("https://x/healthz", opener_returning(b'{"status":"ok"}'))
        )

    def test_application_not_found_404(self):
        err = HTTPError("u", 404, "Not Found", {}, None)
        self.assertIn(
            "HTTP 404", uptime_watch.check_health("https://x/healthz", opener_raising(err))
        )

    def test_unreachable(self):
        self.assertIn(
            "unreachable",
            uptime_watch.check_health("https://x/healthz", opener_raising(URLError("x"))),
        )

    def test_non_json(self):
        self.assertIn(
            "non-JSON", uptime_watch.check_health("https://x/healthz", opener_returning(b"<html>"))
        )


class EvaluateTests(unittest.TestCase):
    def test_all_live_is_healthy(self):
        self.assertEqual(uptime_watch.evaluate(None, LIVE).problems, [])

    def test_removed_and_missing_deployments_alert(self):
        report = uptime_watch.evaluate(
            "HTTP 404",
            [
                {"name": "ari-ai", "deployment": {"status": "REMOVED"}},
                {"name": "Postgres", "deployment": None},
            ],
        )
        self.assertEqual(len(report.problems), 3)
        self.assertIn("service:Postgres:none", report.alert_keys)


class SyncIssueTests(unittest.TestCase):
    def test_creates_then_closes(self):
        down = uptime_watch.evaluate("HTTP 404", LIVE)
        client = FakeClient()
        self.assertEqual(uptime_watch.sync_issue(client, "o/r", down), "created")
        body = client.calls[-1][2]["body"]
        issue = {"number": 7, "title": uptime_watch.ALERT_TITLE, "body": body}
        self.assertEqual(uptime_watch.sync_issue(FakeClient([issue]), "o/r", down), "unchanged")
        closing = FakeClient([issue])
        healthy = uptime_watch.evaluate(None, LIVE)
        self.assertEqual(uptime_watch.sync_issue(closing, "o/r", healthy), "closed")
        self.assertEqual(closing.calls[-1], ("PATCH", "/repos/o/r/issues/7", {"state": "closed"}))

    def test_no_issue_when_healthy(self):
        healthy = uptime_watch.evaluate(None, LIVE)
        self.assertEqual(uptime_watch.sync_issue(FakeClient(), "o/r", healthy), "no open issue")


if __name__ == "__main__":
    unittest.main()
