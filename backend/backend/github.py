"""GitHub integration: webhook signature verification, GitHub App auth, least-privilege reads."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from backend.settings import Settings, get_settings


def verify_signature(secret: bytes, raw_body: bytes, header: str | None) -> bool:
    """Constant-time check of ``X-Hub-Signature-256`` over the exact raw request body."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), header.strip().encode())


def sign(secret: bytes, raw_body: bytes) -> str:
    """Produce a valid signature header (local fixtures and replay tooling)."""
    return "sha256=" + hmac.new(secret, raw_body, hashlib.sha256).hexdigest()


class GitHubNotConfigured(RuntimeError):
    pass


@dataclass
class _Token:
    value: str
    expires_at: float


def _private_key(settings: Settings) -> str | None:
    if settings.github_app_private_key_path:
        value = settings.github_app_private_key_path.get_secret_value()
        if "-----BEGIN" in value:
            return value.replace("\\n", "\n")
        return Path(value).read_text()
    if settings.github_app_private_key:
        return settings.github_app_private_key.get_secret_value().replace("\\n", "\n")
    return None


class GitHubApp:
    """Short-lived installation tokens (ADR-0009). Never uses PATs."""

    def __init__(
        self, settings: Settings | None = None, client: httpx.Client | None = None
    ) -> None:
        self.settings = settings or get_settings()
        key = _private_key(self.settings)
        if not (self.settings.github_app_id and key):
            raise GitHubNotConfigured(
                "GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY_PATH or GITHUB_APP_PRIVATE_KEY not configured"
            )
        self._key = key
        self._client = client or httpx.Client(base_url=self.settings.github_api_url, timeout=20.0)
        self._tokens: dict[tuple[int, bool, str | None], _Token] = {}

    def _app_jwt(self) -> str:
        import jwt

        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": self.settings.github_app_id},
            self._key,
            algorithm="RS256",
        )

    def installation_token(
        self, installation_id: int, *, write: bool = False, repository: str | None = None
    ) -> str:
        # Write tokens exist only for fix delivery (ADR-0021) and are limited to the one
        # repository being delivered; every other use reads.
        key = (installation_id, write, repository)
        cached = self._tokens.get(key)
        if cached and cached.expires_at - time.time() > 120:
            return cached.value
        r = self._client.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {self._app_jwt()}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "permissions": {"contents": "write", "pull_requests": "write", "metadata": "read"}
                if write
                else {"contents": "read", "metadata": "read"}
            }
            | ({"repositories": [repository]} if repository else {}),
        )
        r.raise_for_status()
        data = r.json()
        self._tokens[key] = _Token(data["token"], time.time() + 3000)
        return data["token"]

    def open_pull_request(
        self, installation_id: int, full_name: str, *, head: str, base: str, title: str, body: str
    ) -> str:
        """Open a PR (ADR-0021) and return its URL. Needs the App's pull_requests: write."""
        token = self.installation_token(
            installation_id, write=True, repository=full_name.split("/", 1)[1]
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        r = self._client.post(
            f"/repos/{full_name}/pulls",
            headers=headers,
            json={"title": title, "head": head, "base": base, "body": body},
        )
        if r.status_code == 422 and "already exists" in r.text:  # a retried job: reuse the PR
            owner = full_name.split("/", 1)[0]
            found = self._client.get(
                f"/repos/{full_name}/pulls",
                params={"head": f"{owner}:{head}", "state": "all"},
                headers=headers,
            )
            found.raise_for_status()
            if found.json():
                return str(found.json()[0]["html_url"])
        r.raise_for_status()
        return str(r.json()["html_url"])

    def post_pull_request_review(
        self, installation_id: int, full_name: str, number: int, body: str
    ) -> None:
        """Post one top-level review comment without inline review comments."""
        token = self.installation_token(
            installation_id, write=True, repository=full_name.split("/", 1)[1]
        )
        r = self._client.post(
            f"/repos/{full_name}/pulls/{number}/reviews",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"event": "COMMENT", "body": body},
        )
        r.raise_for_status()

    def pull_request_review_has_marker(
        self, installation_id: int, full_name: str, number: int, marker: str
    ) -> bool:
        """Whether a review on the PR carries ``marker``; reconciles an ambiguous POST."""
        for page in range(1, 11):  # 1000 reviews at most; beyond that the caller cannot tell
            reviews = self._get(
                installation_id,
                f"/repos/{full_name}/pulls/{number}/reviews",
                per_page=100,
                page=page,
            )
            if any(marker in (review.get("body") or "") for review in reviews):
                return True
            if len(reviews) < 100:
                return False
        raise RuntimeError(f"too many reviews on {full_name}#{number} to reconcile")

    def _app_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._app_jwt()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def hook_deliveries(self, *, since: datetime) -> list[dict[str, Any]]:
        """The App's webhook deliveries (newest first) back to ``since``."""
        cutoff = since.strftime("%Y-%m-%dT%H:%M:%SZ")  # GitHub's delivered_at format
        out: list[dict[str, Any]] = []
        params: dict[str, Any] = {"per_page": 100}
        for _ in range(50):  # 5000 deliveries at most
            r = self._client.get("/app/hook/deliveries", params=params, headers=self._app_headers())
            r.raise_for_status()
            page = r.json()
            out.extend(page)
            cursor = r.links.get("next", {}).get("url")
            if not page or not cursor or page[-1]["delivered_at"] < cutoff:
                break
            params = {"per_page": 100, "cursor": httpx.URL(cursor).params.get("cursor")}
        return [d for d in out if d["delivered_at"] >= cutoff]

    def redeliver(self, delivery_id: int) -> None:
        r = self._client.post(
            f"/app/hook/deliveries/{delivery_id}/attempts", headers=self._app_headers()
        )
        r.raise_for_status()

    def _get(self, installation_id: int, path: str, **params: Any) -> Any:
        r = self._client.get(
            path,
            params=params or None,
            headers={
                "Authorization": f"Bearer {self.installation_token(installation_id)}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        r.raise_for_status()
        return r.json()

    def collaborator_permission(
        self, installation_id: int, full_name: str, login: str, github_id: int
    ) -> str:
        """The user's current base permission on the repository: admin, write, read or none.

        ``permission`` maps maintain to write and triage to read, and a custom role to the base
        role it inherits; ``role_name`` can be a custom name, so it is not used to authorize.
        A 404 means GitHub knows no access for them. Logins can be renamed and re-registered,
        so an answer about a different account id counts as no access."""
        try:
            body = self._get(
                installation_id, f"/repos/{full_name}/collaborators/{login}/permission"
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return "none"
            raise
        if (body.get("user") or {}).get("id") != github_id:
            return "none"
        return str(body.get("permission") or "none")

    def user_installation(self, login: str) -> dict[str, Any] | None:
        """Return this App's installation for a GitHub user, if one exists."""
        r = self._client.get(
            f"/users/{quote(login, safe='')}/installation",
            headers={
                "Authorization": f"Bearer {self._app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def installation_repositories(self, installation_id: int) -> list[dict[str, Any]]:
        """List repositories selected for one installation, capped at 1000."""
        repos: list[dict[str, Any]] = []
        for page in range(1, 11):
            data = self._get(installation_id, "/installation/repositories", per_page=100, page=page)
            batch = data.get("repositories") or []
            for repo in batch:
                full_name = repo.get("full_name")
                if isinstance(full_name, str):
                    repos.append(
                        {
                            "full_name": full_name,
                            "installation_id": installation_id,
                            "default_branch": repo.get("default_branch") or "main",
                            "private": bool(repo.get("private")),
                        }
                    )
            if len(batch) < 100:
                break
        return repos

    def compare_commits(
        self, installation_id: int, full_name: str, base: str, head: str
    ) -> list[str]:
        """Enumerate the complete push range; never silently accept a truncated response."""
        shas: list[str] = []
        for page in range(1, 101):
            data = self._get(
                installation_id,
                f"/repos/{full_name}/compare/{base}...{head}",
                per_page=100,
                page=page,
            )
            batch = data.get("commits") or []
            shas.extend(c["sha"] for c in batch)
            if len(shas) >= data["total_commits"]:
                return shas
            if not batch:
                break
        raise ValueError("GitHub compare did not return the complete commit range")

    def recent_commits(
        self, installation_id: int, full_name: str, branch: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Newest-first commits on `branch`, trimmed to what the commit list shows."""
        data = self._get(
            installation_id, f"/repos/{full_name}/commits", sha=branch, per_page=min(limit, 100)
        )
        out = []
        for c in data if isinstance(data, list) else []:
            commit = c.get("commit") or {}
            author = commit.get("author") or {}
            login = (c.get("author") or {}).get("login")
            out.append(
                {
                    "sha": c.get("sha"),
                    "message": (commit.get("message") or "").split("\n", 1)[0][:300],
                    "author": login or author.get("name") or "unknown",
                    "committed_at": author.get("date"),
                    "html_url": c.get("html_url"),
                }
            )
        return [c for c in out if isinstance(c["sha"], str) and len(c["sha"]) == 40]

    def compare_paths(
        self, installation_id: int, full_name: str, base: str, head: str
    ) -> tuple[list[str], bool]:
        """Changed paths between two SHAs. Returns (paths, complete)."""
        data = self._get(
            installation_id, f"/repos/{full_name}/compare/{base}...{head}", per_page=100
        )
        files = data.get("files") or []
        paths = sorted({f["filename"] for f in files if isinstance(f.get("filename"), str)})
        return paths, len(files) < 300

    def commit(self, installation_id: int, full_name: str, sha: str) -> dict[str, Any]:
        return self._get(installation_id, f"/repos/{full_name}/commits/{sha}")


def user_repositories(
    access_token: str, settings: Settings | None = None, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    """Repositories the user can reach through this App's installations.

    Uses the user-to-server token from the App's own sign-in (`/user/installations`), so
    the list is exactly the intersection of what the user can see and what the App was
    installed on. Capped at 1000 repositories.
    """
    s = settings or get_settings()
    http = client or httpx.Client(base_url=s.github_api_url, timeout=20.0)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    def pages(path: str, key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in range(1, 11):
            r = http.get(path, params={"per_page": 100, "page": page}, headers=headers)
            r.raise_for_status()
            batch = r.json().get(key) or []
            out.extend(batch)
            if len(batch) < 100:
                break
        return out

    repos: list[dict[str, Any]] = []
    for inst in pages("/user/installations", "installations"):
        inst_id = inst.get("id")
        if not isinstance(inst_id, int):
            continue
        for repo in pages(f"/user/installations/{inst_id}/repositories", "repositories"):
            name = repo.get("full_name")
            if isinstance(name, str):
                repos.append(
                    {
                        "full_name": name,
                        "installation_id": inst_id,
                        "default_branch": repo.get("default_branch") or "main",
                        "private": bool(repo.get("private")),
                    }
                )
    return repos[:1000]
