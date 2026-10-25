"""Shared fixtures. All tests run against the owned `examples/payments-lite` target."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from security_agents.checks.library import AUTHENTICATION, BOUNDS, OWNERSHIP, CheckLibrary, default_library
from security_agents.contracts import (
    AuthorizedScope,
    Budget,
    ScopeStatus,
    ScopeTarget,
)
from security_agents.intel import FindingsHistory, LocalIntel, load_expectations
from security_agents.sandbox.driver import LocalProcessDriver
from security_agents.sandbox.workspace import SnapshotHandle, copy_tree
from security_agents.tools import AuditSink, ToolContext, default_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
PAYMENTS_LITE_DIR = REPO_ROOT / "examples" / "payments-lite"
PAYMENTS_LITE = PAYMENTS_LITE_DIR / "target"  # the platform indexes target/ only
LABELS = PAYMENTS_LITE_DIR / "labels.yaml"
BUILD_RECIPE = PAYMENTS_LITE_DIR / "build_recipe.json"
TARGET_ID = "tgt_payments_lite"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def payments_lite_src() -> Path:
    assert PAYMENTS_LITE.is_dir(), f"missing owned fixture target at {PAYMENTS_LITE}"
    return PAYMENTS_LITE


@pytest.fixture
def snapshot(tmp_path: Path, payments_lite_src: Path) -> SnapshotHandle:
    """Pinned copy of target/ plus the scope-approved build recipe.

    The recipe lives beside the target (not inside it) in the repository; the
    platform stages it with the snapshot because a check template needs it.
    """
    staging = tmp_path / "staging"
    copy_tree(payments_lite_src, staging)
    shutil.copy2(BUILD_RECIPE, staging / "build_recipe.json")
    return SnapshotHandle.from_directory(
        staging, tmp_path / "snapshot", snapshot_id="snp_test", repo_id="rep_payments_lite"
    )


@pytest.fixture
def intel(snapshot: SnapshotHandle) -> LocalIntel:
    """Index of target/, with the labelled expectations injected (pending INTEL-007)."""
    return LocalIntel(
        snapshot,
        expectations=load_expectations(
            LABELS,
            snapshot_id=snapshot.snapshot_id,
            commit_sha=snapshot.commit_sha,
            strip_prefix="target/",
        ),
    )


@pytest.fixture
def recipe() -> dict:
    return json.loads(BUILD_RECIPE.read_text())


@pytest.fixture
def library() -> CheckLibrary:
    return default_library()


@pytest.fixture
def driver() -> LocalProcessDriver:
    # Dev driver, explicitly allowed for the owned fixture target only.
    return LocalProcessDriver(allow=True)


@pytest.fixture
def history() -> FindingsHistory:
    return FindingsHistory()


def make_scope(
    *,
    base: datetime | None = None,
    status: ScopeStatus = ScopeStatus.ACTIVE,
    expires_at: datetime | None = None,
    approved_by: str | None = "usr_approver",
    requested_by: str = "usr_requester",
    categories: list[str] | None = None,
    target_id: str = TARGET_ID,
) -> AuthorizedScope:
    base = base or NOW
    return AuthorizedScope(
        id="scp_test",
        project_id="prj_test",
        name="payments-lite sandbox",
        targets=[
            ScopeTarget(
                target_id=target_id,
                repo_id="rep_payments_lite",
                build_recipe_ref="build_recipe.json",
                exposed_ports=[8000],
            )
        ],
        allowed_check_categories=categories
        if categories is not None
        else [OWNERSHIP.category, BOUNDS.category, AUTHENTICATION.category],
        status=status,
        requested_by=requested_by,
        approved_by=approved_by,
        approved_at=base - timedelta(hours=1) if approved_by else None,
        expires_at=expires_at or (base + timedelta(hours=8)),
        notes="owned demo target",
    )


def live_scope(**kw) -> AuthorizedScope:
    """Scope valid against the real wall clock (for orchestrator runs)."""
    return make_scope(base=datetime.now(UTC), **kw)


@pytest.fixture
def scope() -> AuthorizedScope:
    return make_scope()


@pytest.fixture
def budget() -> Budget:
    return Budget(max_tokens=400_000, max_usd=5.0, max_wall_clock_s=600.0)


@pytest.fixture
def registry():
    return default_registry()


def make_ctx(
    agent,
    *,
    intel,
    scope=None,
    services: dict | None = None,
    step_key: str = "step",
    kill_switch=lambda: False,
    now=lambda: NOW,
    org_member_count: int = 1,
) -> ToolContext:
    base = {"intel": intel, "history": FindingsHistory(), "checks": default_library()}
    base.update(services or {})
    return ToolContext(
        assessment_id="asm_test",
        agent=agent,
        run_id="run_test",
        step_key=step_key,
        read_root=intel.snapshot.root,
        services=base,
        scope=scope,
        org_member_count=org_member_count,
        audit=AuditSink(),
        kill_switch=kill_switch,
        now=now,
    )
