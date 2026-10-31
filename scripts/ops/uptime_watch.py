"""Production uptime watchdog: opens an ops-alert issue when prod is down.

Checks the backend health endpoint and that every Railway service in the
project has a live (SUCCESS) deployment. A removed or crashed deployment
serves Railway's "Application not found" page and nothing in the product
works, which the storage watchdog cannot see.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_SPEC = importlib.util.spec_from_file_location(
    "storage_watch", Path(__file__).resolve().parent / "storage_watch.py"
)
assert _SPEC is not None and _SPEC.loader is not None
storage_watch = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault(_SPEC.name, storage_watch)
_SPEC.loader.exec_module(storage_watch)

ALERT_TITLE = "[ops-alert] Production is down"
DEFAULT_HEALTH_URL = "https://ari-ai-production-76d4.up.railway.app/healthz"
LIVE_STATUSES = {"SUCCESS", "SLEEPING"}


@dataclass
class UptimeReport:
    lines: list[str]
    problems: list[str]
    alert_keys: list[str]


def check_health(url: str, opener: Any = urlopen) -> str | None:
    request = Request(url, headers={"User-Agent": "curl/8.7.1"})
    try:
        with opener(request, timeout=20) as response:
            body = response.read()
    except HTTPError as error:
        return f"{url} returned HTTP {error.code}"
    except (URLError, TimeoutError, OSError):
        return f"{url} is unreachable"
    try:
        status = json.loads(body).get("status")
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return f"{url} returned a non-JSON body"
    if status != "ok":
        return f"{url} reported status {status!r}"
    return None


def fetch_services(token: str) -> list[dict[str, Any]]:
    data = storage_watch._railway_graphql(token, "{projectToken{projectId environmentId}}")
    ids = data.get("projectToken") or {}
    project_id, environment_id = ids.get("projectId"), ids.get("environmentId")
    if not project_id or not environment_id:
        raise RuntimeError("Railway project token did not return project/environment IDs")
    query = (
        f"{{project(id:{json.dumps(project_id)}){{services{{edges{{node{{name "
        "serviceInstances{edges{node{environmentId latestDeployment{status createdAt}}}}"
        "}}}}}"
    )
    project = _railway_project(token, query)
    services: list[dict[str, Any]] = []
    for edge in project.get("services", {}).get("edges", []):
        node = edge.get("node") or {}
        for inst in node.get("serviceInstances", {}).get("edges", []):
            instance = inst.get("node") or {}
            if instance.get("environmentId") != environment_id:
                continue
            services.append(
                {"name": node.get("name"), "deployment": instance.get("latestDeployment")}
            )
    return services


def _railway_project(token: str, query: str) -> dict[str, Any]:
    project = storage_watch._railway_graphql(token, query).get("project")
    if not isinstance(project, dict):
        raise RuntimeError("Railway project query returned no project")
    return project


def evaluate(health_problem: str | None, services: Sequence[Mapping[str, Any]]) -> UptimeReport:
    lines = ["| Service | Latest deployment |", "|---|---|"]
    problems: list[str] = []
    keys: list[str] = []
    if health_problem:
        problems.append(f"Backend health check failed: {health_problem}")
        keys.append("health")
    for service in services:
        name = str(service.get("name"))
        deployment = service.get("deployment") or {}
        status = deployment.get("status") or "none"
        lines.append(f"| {name} | {status} |")
        if status not in LIVE_STATUSES:
            problems.append(
                f"Railway service `{name}` has no live deployment (latest: {status}). "
                "Redeploy it from the Railway dashboard (service → Deployments → Redeploy)."
            )
            keys.append(f"service:{name}:{status}")
    return UptimeReport(lines=lines, problems=problems, alert_keys=keys)


def render_markdown(report: UptimeReport) -> str:
    out = ["# Production uptime watchdog", "", *report.lines, "", "## Problems"]
    out.extend(f"- {p}" for p in report.problems) if report.problems else out.append("- None")
    return "\n".join(out)


def sync_issue(client: Any, repo: str, report: UptimeReport) -> str:
    issues = client.get(f"/repos/{repo}/issues?state=open&per_page=100")
    if not isinstance(issues, list):
        raise RuntimeError("GitHub returned an unexpected issue list")
    issue = next(
        (i for i in issues if i.get("title") == ALERT_TITLE and "pull_request" not in i), None
    )
    markdown = render_markdown(report)
    digest = hashlib.sha256("\n".join(sorted(report.alert_keys)).encode()).hexdigest()[:16]
    tag = f"<!-- uptime-watch:{digest} -->"
    if report.problems:
        body = f"{markdown}\n\n{tag}\n"
        if issue is None:
            client.post(f"/repos/{repo}/issues", {"title": ALERT_TITLE, "body": body})
            return "created"
        if tag in str(issue.get("body") or ""):
            return "unchanged"
        client.post(f"/repos/{repo}/issues/{issue['number']}/comments", {"body": markdown})
        client.patch(f"/repos/{repo}/issues/{issue['number']}", {"body": body})
        return "updated"
    if issue is None:
        return "no open issue"
    client.post(
        f"/repos/{repo}/issues/{issue['number']}/comments", {"body": f"Recovered\n\n{markdown}"}
    )
    client.patch(f"/repos/{repo}/issues/{issue['number']}", {"state": "closed"})
    return "closed"


def main() -> int:
    token = os.environ.get("RAILWAY_TOKEN", "")
    health_problem = check_health(os.getenv("UPTIME_HEALTH_URL", DEFAULT_HEALTH_URL))
    services: list[dict[str, Any]] = []
    if token:
        try:
            services = fetch_services(token)
        except Exception as error:
            message = str(error).replace(token, "[REDACTED]")
            services = [{"name": "railway-api", "deployment": {"status": f"unknown ({message})"}}]
    report = evaluate(health_problem, services)
    markdown = render_markdown(report)
    print(markdown)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(f"{markdown}\n")
    github_token, repository = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPOSITORY")
    if github_token and repository:
        print(
            f"Issue sync: {sync_issue(storage_watch.GitHubClient(github_token), repository, report)}"
        )
    return 1 if report.problems else 0


if __name__ == "__main__":
    sys.exit(main())
