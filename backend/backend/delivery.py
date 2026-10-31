"""Fix delivery (ADR-0021): pre-approved remediation and delivery of Verify-accepted patches.

``ProjectSettings.delivery_mode``:

* ``auto``: remediation approvals are approved in the name of the owner who
  authorized delivery, and each patch whose independent Verify verdict is ``accepted`` is
  committed onto the default branch. If the branch moved since the assessed commit, or the
  push is refused (branch protection), Ari opens a pull request instead.
* ``pull_request`` (pilot default for new projects): same pre-approval, but the fix always
  arrives as a PR for the team.
* ``manual``: nothing here acts; a human approves each remediation and nothing is pushed.

Nothing is ever force-pushed, and a rejected, inconclusive or unverified patch never leaves
the sandbox. Before delivery, the default-branch tip is checked so an already-applied fix is
not delivered twice. A project that predates delivery modes has no recorded authorizer, so it
stays manual until an owner saves its settings.
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.engine import Connection

from backend import audit, jobs
from backend.artifacts import ArtifactStore
from backend.checkouts import FULL_NAME_RE, CheckoutError, _git, _locked, auth_env, fetch_commit
from backend.connection_guard import RepositoryDetached, github_write
from backend.db import (
    approvals,
    assessments,
    patch_candidates,
    repositories,
    transaction,
    verification_results,
)
from backend.redaction import find_high_confidence_secret
from backend.services import project_org, project_settings
from backend.settings import get_settings
from backend.worker import JobContext, PermanentJobError, handler
from shared.audit import ActorType
from shared.base import utcnow
from shared.errors import ErrorCode
from shared.evidence import ArtifactKind
from shared.findings import PatchCandidate, VerificationResult
from shared.scope import Approval, ApprovalStatus, ApprovalSubject
from shared.tenancy import DeliveryMode, MemberRole

BRANCH_RE = re.compile(r"^[A-Za-z0-9._/\-]+$")
COMMIT_AUTHOR = ("Ari", "ari@users.noreply.github.com")
log = logging.getLogger(__name__)


class BranchMoved(CheckoutError):
    """The branch no longer points at the commit that was assessed and verified."""


def authority(conn: Connection, project_id: str) -> tuple[DeliveryMode, str] | None:
    """(mode, authorizing owner) when delivery is automatic, else None.

    The authorizer must still be an owner of the project's organization: a removed or
    demoted owner's standing authorization ends with their access."""
    from backend.api.deps import org_role

    s = project_settings(conn, project_id)
    if s.delivery_mode is DeliveryMode.manual or not s.delivery_authorized_by:
        return None
    role = org_role(conn, s.delivery_authorized_by, project_org(conn, project_id))
    if role is not MemberRole.owner:
        return None
    return s.delivery_mode, s.delivery_authorized_by


def auto_approve_pending(conn: Connection, assessment_id: str, project_id: str) -> list[str]:
    """Approve this assessment's pending remediation approvals on the authorizing owner's
    behalf and enqueue the resume. Returns the approval ids (empty when manual)."""
    granted = authority(conn, project_id)
    if granted is None:
        return []
    approver = granted[1]
    rows = (
        conn.execute(
            sa.select(approvals.c.doc)
            .where(
                approvals.c.assessment_id == assessment_id,
                approvals.c.subject_type == ApprovalSubject.remediation.value,
                approvals.c.status == ApprovalStatus.pending.value,
            )
            .order_by(approvals.c.created_at)
            .with_for_update()
        )
        .scalars()
        .all()
    )
    org_id = project_org(conn, project_id)
    ids = []
    for doc in rows:
        ap = Approval.model_validate(doc).model_copy(
            update={
                "status": ApprovalStatus.approved,
                "decided_by": approver,
                "decided_at": utcnow(),
                "reason": "auto delivery: pre-approved by the project owner",
            }
        )
        conn.execute(
            sa.update(approvals)
            .where(approvals.c.id == ap.id)
            .values(status=ap.status.value, doc=ap.model_dump(mode="json"))
        )
        audit.record(
            conn,
            org_id=org_id,
            project_id=project_id,
            actor_type=ActorType.system,
            actor_id="auto-delivery",
            action="approval.auto_approved",
            subject_type="approval",
            subject_id=ap.id,
            details={"on_behalf_of": approver, "subject_id": ap.subject_id},
        )
        ids.append(ap.id)
    if ids:
        jobs.enqueue(
            conn,
            "run_assessment",
            {"assessment_id": assessment_id},
            dedupe_key=f"asm:{assessment_id}:resume:auto:{ids[-1]}",
        )
    return ids


def approve_waiting_runs(conn: Connection, project_id: str) -> None:
    """Called when delivery is switched on: unblock runs already waiting for approval."""
    waiting = (
        conn.execute(
            sa.select(approvals.c.assessment_id)
            .where(
                approvals.c.project_id == project_id,
                approvals.c.subject_type == ApprovalSubject.remediation.value,
                approvals.c.status == ApprovalStatus.pending.value,
                approvals.c.assessment_id.is_not(None),
            )
            .distinct()
        )
        .scalars()
        .all()
    )
    for assessment_id in waiting:
        auto_approve_pending(conn, assessment_id, project_id)


def push_patch(
    repo_dir: Path,
    full_name: str,
    base_sha: str,
    diff: bytes,
    token: str,
    message: str,
    branch: str,
    *,
    require_tip: bool = False,
    github_web_url: str = "https://github.com",
) -> str:
    """Commit ``diff`` on ``base_sha`` and push it to ``refs/heads/branch``, never forced.

    ``require_tip``: the branch must still point at ``base_sha`` (a direct push to the default
    branch ships exactly the tested tree). Returns the new commit SHA."""
    if not FULL_NAME_RE.match(full_name):
        raise CheckoutError(f"invalid repository name {full_name!r}")
    if not BRANCH_RE.match(branch) or ".." in branch:
        raise CheckoutError(f"invalid branch name {branch!r}")
    url = f"{github_web_url.rstrip('/')}/{full_name}.git"
    env = auth_env(token)
    if require_tip:
        tip_ref = "refs/ari/delivery-tip"
        _git(
            "-C", str(repo_dir), "fetch", "--quiet", "--depth=1", "--no-tags",
            url, f"+refs/heads/{branch}:{tip_ref}", extra_env=env,
        )  # fmt: skip
        if _git("-C", str(repo_dir), "rev-parse", tip_ref).strip() != base_sha:
            raise BranchMoved(f"{branch} moved since the assessed commit")
    elif published := _git("ls-remote", url, f"refs/heads/{branch}", extra_env=env).split():
        return published[0]  # an earlier attempt of this job already published the branch
    with tempfile.TemporaryDirectory(prefix="ari-delivery-") as tmp:
        wt = Path(tmp) / "wt"
        patch_file = Path(tmp) / "fix.diff"
        patch_file.write_bytes(diff)
        _git("-C", str(repo_dir), "worktree", "add", "--quiet", "--detach", str(wt), base_sha)
        try:
            try:
                _git("-C", str(wt), "apply", "--index", str(patch_file))
            except CheckoutError as exc:
                raise CheckoutError(f"patch does not apply to {base_sha[:12]}: {exc}") from exc
            name, email = COMMIT_AUTHOR
            _git(
                "-C", str(wt), "-c", f"user.name={name}", "-c", f"user.email={email}",
                "commit", "--quiet", "--no-verify", "-m", message,
            )  # fmt: skip
            sha = _git("-C", str(wt), "rev-parse", "HEAD").strip()
            _git("-C", str(wt), "push", "--quiet", url, f"HEAD:refs/heads/{branch}", extra_env=env)
        finally:
            _git("-C", str(repo_dir), "worktree", "remove", "--force", str(wt))
    return sha


def default_tip_state(
    repo_dir: Path,
    full_name: str,
    branch: str,
    diff: bytes,
    token: str,
    *,
    base_sha: str,
    github_web_url: str,
) -> tuple[str, str]:
    """Classify whether the verified diff applies to the latest default-branch tip."""
    try:
        if not FULL_NAME_RE.match(full_name):
            raise CheckoutError(f"invalid repository name {full_name!r}")
        if not BRANCH_RE.match(branch) or ".." in branch:
            raise CheckoutError(f"invalid branch name {branch!r}")

        url = f"{github_web_url.rstrip('/')}/{full_name}.git"
        env = auth_env(token)
        tip_ref = f"refs/ari/tip/{branch}"
        _git(
            "-C", str(repo_dir), "fetch", "--quiet", "--depth=1", "--no-tags",
            url, f"+refs/heads/{branch}:{tip_ref}", extra_env=env, timeout=60,
        )  # fmt: skip
        tip = _git("-C", str(repo_dir), "rev-parse", tip_ref).strip()
        if tip == base_sha:
            return tip, "same"

        with tempfile.TemporaryDirectory(prefix="ari-tip-check-") as tmp:
            worktree = Path(tmp) / "wt"
            patch_file = Path(tmp) / "fix.diff"
            patch_file.write_bytes(diff)
            try:
                _git(
                    "-C", str(repo_dir), "worktree", "add", "--quiet", "--detach",
                    str(worktree), tip, timeout=60,
                )  # fmt: skip
                try:
                    _git("-C", str(worktree), "apply", "--check", str(patch_file), timeout=60)
                except CheckoutError:
                    try:
                        _git(
                            "-C", str(worktree), "apply", "--check", "-R",
                            str(patch_file), timeout=60,
                        )  # fmt: skip
                    except CheckoutError:
                        return tip, "conflicts"
                    return tip, "already_applied"
                return tip, "applies"
            finally:
                try:
                    _git(
                        "-C", str(repo_dir), "worktree", "remove", "--force",
                        str(worktree), timeout=60,
                    )  # fmt: skip
                except (CheckoutError, subprocess.TimeoutExpired) as exc:
                    log.warning("could not remove default-branch tip worktree: %s", exc)
    except subprocess.TimeoutExpired as exc:
        raise CheckoutError(f"default-branch tip check timed out after {exc.timeout:.0f}s") from exc


def _record(
    conn: Connection, org_id: str, project_id: str, action: str, details: dict[str, Any]
) -> None:
    audit.record(
        conn,
        org_id=org_id,
        project_id=project_id,
        actor_type=ActorType.system,
        actor_id="auto-delivery",
        action=action,
        subject_type="patch",
        subject_id=str(details["patch_id"]),
        details=details,
    )


def _transient(exc: Exception) -> bool:
    """Timeouts, resets, rate limits and GitHub 5xx are worth a queue retry."""
    if isinstance(exc, httpx.HTTPStatusError):
        r = exc.response
        return r.status_code == 429 or r.status_code >= 500 or "rate limit" in r.text.lower()
    return isinstance(exc, httpx.TransportError)


def _unverified_bytes(
    conn: Connection, verification: VerificationResult, patch: Any, diff: bytes
) -> str | None:
    """Why ``diff`` is not the exact byte string Verify executed, or None when it is.

    The stored artifact may be a redacted copy; Verify binds the executed bytes' hash to it
    as evidence on diff_scope_ok. Delivery refuses anything that cannot be tied to that hash."""
    import hashlib
    import json

    got = hashlib.sha256(diff).hexdigest()
    verified = patch.diff_sha256
    for check in verification.checks:
        if check.name.value != "diff_scope_ok":
            continue
        for ev in check.evidence:
            if not ev.artifact_id:
                continue
            try:
                doc = json.loads(ArtifactStore().get(conn, ev.artifact_id)[1])
            except (KeyError, OSError, ValueError):
                continue
            if isinstance(doc, dict) and doc.get("patch_id") == patch.id and "redacted" in doc:
                if doc["redacted"]:
                    return "the stored diff was redacted; Verify tested different bytes"
                verified = doc.get("verified_diff_sha256")
    return None if got == verified else "stored diff does not match the bytes Verify accepted"


def _verified_diff_bytes(
    conn: Connection, verification: VerificationResult, patch: Any
) -> tuple[bytes | None, str | None]:
    import hashlib
    import json

    store = ArtifactStore()
    binding: dict[str, Any] = {}
    for check in verification.checks:
        if check.name.value != "diff_scope_ok":
            continue
        for ev in check.evidence:
            if not ev.artifact_id:
                continue
            try:
                doc = json.loads(store.get(conn, ev.artifact_id)[1])
            except (KeyError, OSError, ValueError):
                continue
            if isinstance(doc, dict) and doc.get("patch_id") == patch.id:
                binding = doc
                break
        if binding:
            break

    sealed_id = binding.get("sealed_diff_artifact_id")
    if sealed_id:
        try:
            meta, data = store.get(conn, sealed_id)
        except (KeyError, OSError):
            return None, "sealed diff does not match the bytes Verify accepted"
        if meta["kind"] != ArtifactKind.sealed_diff.value:
            return None, "sealed diff does not match the bytes Verify accepted"
        actual_sha256 = hashlib.sha256(data).hexdigest()
        if (
            actual_sha256 != binding.get("verified_diff_sha256")
            or binding.get("stored_diff_artifact_id") != patch.diff_artifact_id
            or binding.get("stored_diff_sha256") != patch.diff_sha256
        ):
            return None, "sealed diff does not match the bytes Verify accepted"
        secret = find_high_confidence_secret(data.decode("utf-8", errors="replace"))
        if secret:
            return None, f"verified diff contains a credential ({secret}); refusing to deliver"
        return data, None

    try:
        _, data = store.get(conn, patch.diff_artifact_id)
    except (KeyError, OSError):
        return None, "stored diff artifact is missing"
    mismatch = _unverified_bytes(conn, verification, patch, data)
    return (None, mismatch) if mismatch else (data, None)


def _reason(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        hint = " (grant the GitHub App Contents and Pull requests: Read & write)"
        return f"GitHub answered {code}{hint if code in (403, 404) else ''}"
    return str(exc)[:300]


@handler("deliver_verified_patch")
def deliver_verified_patch(ctx: JobContext) -> dict[str, Any]:
    ctx.check_cancelled()
    s = get_settings()
    with transaction() as conn:
        verification = VerificationResult.model_validate(
            conn.execute(
                sa.select(verification_results.c.doc).where(
                    verification_results.c.id == ctx.job.payload["verification_id"]
                )
            ).scalar_one()
        )
        if verification.verdict.value != "accepted":
            raise PermanentJobError(
                ErrorCode.FORBIDDEN, "only Verify-accepted patches are delivered"
            )
        patch = PatchCandidate.model_validate(
            conn.execute(
                sa.select(patch_candidates.c.doc).where(
                    patch_candidates.c.id == verification.patch_id
                )
            ).scalar_one()
        )
        asm = (
            conn.execute(
                sa.select(assessments.c.project_id, assessments.c.repo_id).where(
                    assessments.c.id == ctx.job.payload["assessment_id"]
                )
            )
            .mappings()
            .one()
        )
        project_id = asm["project_id"]
        org_id = project_org(conn, project_id)
        details: dict[str, Any] = {
            "patch_id": patch.id,
            "verification_id": verification.id,
            "finding_id": patch.finding_id,
        }
        granted = authority(conn, project_id)
        repo = (
            conn.execute(sa.select(repositories).where(repositories.c.id == asm["repo_id"]))
            .mappings()
            .first()
        )
        connected = repo is not None and repo["installation_id"] is not None
        diff, mismatch = None, None
        if granted and connected:
            diff, mismatch = _verified_diff_bytes(conn, verification, patch)

    def finish(action: str, **more: Any) -> None:
        with transaction() as conn:  # own transaction: a raise afterwards must not roll it back
            _record(conn, org_id, project_id, action, details | more)

    if granted is None:  # switched to manual (or authorizer lost ownership) since queueing
        finish("delivery.skipped")
        return {"status": "skipped", "reason": "delivery is manual"}
    if repo is not None and repo["disconnected_at"] is not None:
        finish("delivery.skipped", reason="repository is disconnected")
        return {"status": "skipped", "reason": "repository is disconnected"}
    if not connected or repo is None or s.security_workdir is None:
        finish("delivery.failed", reason="repository is not connected through the GitHub App")
        raise PermanentJobError(
            ErrorCode.SUBSYSTEM_UNAVAILABLE, "repository is not connected through the GitHub App"
        )

    if mismatch or diff is None:
        finish("delivery.failed", reason=mismatch)
        raise PermanentJobError(ErrorCode.CONFLICT_STATE, mismatch or "verified diff missing")

    from backend.github import GitHubApp

    mode = granted[0]
    if mode is DeliveryMode.auto and not s.auto_delivery_allowed:
        mode = DeliveryMode.pull_request  # settings saved before the gate; never push direct
    full_name, default_branch = str(repo["full_name"]), str(repo["default_branch"])
    iid = int(repo["installation_id"])
    repo_dir = s.security_workdir / "repos" / f"{repo['id']}.git"
    base = patch.base_commit_sha
    web = s.github_web_url
    message = (
        f"fix: {patch.rationale}\n\n"
        f"Applied by Ari. Finding {patch.finding_id}, patch {patch.id},\n"
        f"independently verified by {verification.id}."
    )
    fallback: str | None = None
    tip: str | None = None
    tip_state = "unknown"

    try:
        app = GitHubApp(settings=s)
        # Minting the write token is itself a provider write-access request: hold the write
        # lease so a disconnect that has returned 200 can never be followed by a new token.
        with github_write(str(repo["id"]), iid):
            token = app.installation_token(iid, write=True, repository=full_name.split("/", 1)[1])
        with _locked(repo_dir):
            fetch_commit(repo_dir, full_name, base, token, github_web_url=web)
            try:
                tip, tip_state = default_tip_state(
                    repo_dir,
                    full_name,
                    default_branch,
                    diff,
                    token,
                    base_sha=base,
                    github_web_url=web,
                )
            except CheckoutError as exc:
                log.warning("could not inspect %s default-branch tip: %s", full_name, exc)
                tip_state = "unknown"
            if tip is not None and tip_state == "already_applied":
                reason = f"{default_branch} already contains this fix (at {tip[:12]})"
                finish("delivery.skipped", reason=reason, tip_sha=tip)
                return {"status": "skipped", "reason": reason}
            if mode is DeliveryMode.auto:
                try:
                    with github_write(str(repo["id"]), iid):
                        sha = push_patch(
                            repo_dir, full_name, base, diff, token, message, default_branch,
                            require_tip=True, github_web_url=web,
                        )  # fmt: skip
                    finish("delivery.pushed", commit_sha=sha, branch=default_branch)
                    return {"status": "pushed", "commit_sha": sha, "branch": default_branch}
                except CheckoutError as exc:  # branch moved, or push refused (protection)
                    fallback = _reason(exc)
            branch = f"ari/fix-{patch.id}"
            with github_write(str(repo["id"]), iid):
                sha = push_patch(
                    repo_dir, full_name, base, diff, token, message, branch, github_web_url=web
                )
        pr_body = (
            f"Verified fix from Ari for finding `{patch.finding_id}` (patch `{patch.id}`, "
            f"verification `{verification.id}`). Review and merge to apply."
        )
        if tip is not None and tip_state == "applies":
            pr_body += (
                f"\n\nBased on the assessed commit `{base[:12]}`; `{default_branch}` has since "
                f"moved to `{tip[:12]}` and this change still applies cleanly."
            )
        elif tip is not None and tip_state == "conflicts":
            pr_body += (
                f"\n\nBased on the assessed commit `{base[:12]}`; `{default_branch}` has since "
                f"moved to `{tip[:12]}` and this change no longer applies cleanly. Rebase it or "
                "re-run the review on the latest commit."
            )
        with github_write(str(repo["id"]), iid):
            url = app.open_pull_request(
                iid,
                full_name,
                head=branch,
                base=default_branch,
                title=f"fix: {patch.rationale}"[:120],
                body=pr_body,
            )
    except RepositoryDetached as exc:
        finish("delivery.skipped", reason=exc.reason, fallback_from=fallback)
        return {"status": "skipped", "reason": exc.reason}
    except (CheckoutError, httpx.HTTPError) as exc:
        if isinstance(exc, httpx.HTTPError) and _transient(exc):
            raise  # the queue retries with backoff; the branch and PR steps are idempotent
        finish("delivery.failed", reason=_reason(exc), fallback_from=fallback)
        raise PermanentJobError(ErrorCode.CONFLICT_STATE, _reason(exc)) from exc
    finish("delivery.pr_opened", commit_sha=sha, branch=branch, url=url, fallback_from=fallback)
    return {"status": "pr_opened", "url": url, "branch": branch}
