"""Corpus materializer for the investigation-quality eval.

Reads cases.json, copies the named skeleton's ``target/`` tree plus its
``build_recipe.json`` into a fresh staging directory (exactly like
``security_agents.cli`` does), then applies each commit's edits in order.
The tree digest after each commit stage stands in for a commit sha — the same
``tree_digest`` the platform uses to pin directory snapshots.

``ground_truth`` is never written into the staging tree. ``labels.yaml`` is
read by the runner directly from the skeleton (it is context, not an answer
key); nothing in this module lets GT text reach a materialized workspace.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from security_agents.sandbox.workspace import copy_tree, tree_digest

EVAL_ROOT = Path(__file__).resolve().parent
CASES_JSON = EVAL_ROOT / "cases.json"
SKELETONS = EVAL_ROOT / "corpus" / "skeletons"


@dataclass(frozen=True)
class Edit:
    path: str
    mode: str = "replace"  # replace | create_file | append
    old: str = ""
    new: str = ""
    content: str = ""


@dataclass(frozen=True)
class CommitSpec:
    message: str
    edits: tuple[Edit, ...]
    extra_files: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class GroundTruthDefect:
    cwe: str
    path: str
    symbol: str | None
    note: str
    introduced_at_commit: int


@dataclass(frozen=True)
class GroundTruthNegative:
    path: str
    symbol: str | None
    hypothesis_class: str
    note: str


@dataclass(frozen=True)
class Case:
    id: str
    kind: str  # vuln | hard_negative | benign | ambiguous
    skeleton: str
    structure: str
    commits: tuple[CommitSpec, ...]
    defects: tuple[GroundTruthDefect, ...]
    negatives: tuple[GroundTruthNegative, ...]
    expected: dict


@dataclass
class Materialized:
    case: Case
    workspace: Path  # staging dir: target contents + build_recipe.json
    labels_path: Path  # skeleton labels.yaml (context for LocalIntel)
    target_dir_name: str  # recipe's target_dir (for strip_prefix)
    commit_shas: list[str] = field(default_factory=list)  # one per applied commit
    applied_edits: int = 0


def load_cases(path: Path = CASES_JSON) -> list[Case]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases: list[Case] = []
    for c in raw["cases"]:
        commits = tuple(
            CommitSpec(
                message=k["message"],
                edits=tuple(
                    Edit(
                        path=e["path"],
                        mode=e.get("mode", "replace"),
                        old=e.get("old", ""),
                        new=e.get("new", ""),
                        content=e.get("content", ""),
                    )
                    for e in k.get("edits", [])
                ),
                extra_files=tuple(sorted(k.get("extra_files", {}).items())),
            )
            for k in c["commits"]
        )
        gt = c["ground_truth"]
        cases.append(
            Case(
                id=c["id"],
                kind=c["kind"],
                skeleton=c["skeleton"],
                structure=c["structure"],
                commits=commits,
                defects=tuple(
                    GroundTruthDefect(
                        cwe=d["cwe"],
                        path=d["path"],
                        symbol=d.get("symbol"),
                        note=d.get("note", ""),
                        introduced_at_commit=d.get("introduced_at_commit", 0),
                    )
                    for d in gt.get("defects", [])
                ),
                negatives=tuple(
                    GroundTruthNegative(
                        path=n["path"],
                        symbol=n.get("symbol"),
                        hypothesis_class=n["hypothesis_class"],
                        note=n.get("note", ""),
                    )
                    for n in gt.get("negatives", [])
                ),
                expected=c.get("expected", {}),
            )
        )
    return cases


def _apply_edit(root: Path, edit: Edit) -> None:
    rel = edit.path
    target = (root / rel).resolve()
    if root.resolve() not in target.parents:
        raise ValueError(f"edit path escapes workspace: {rel}")
    if edit.mode == "create_file":
        if target.exists():
            raise ValueError(f"create_file target already exists: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(edit.content, encoding="utf-8")
        return
    if not target.is_file():
        raise ValueError(f"edit target missing: {rel}")
    if edit.mode == "append":
        with target.open("a", encoding="utf-8") as fh:
            fh.write(edit.content)
        return
    text = target.read_text(encoding="utf-8")
    count = text.count(edit.old)
    if count != 1:
        raise ValueError(f"edit anchor for {rel} matched {count} times (need exactly 1)")
    target.write_text(text.replace(edit.old, edit.new, 1), encoding="utf-8")


def materialize_case(case: Case, dest_root: Path) -> Materialized:
    """Build the assessed workspace for one case under ``dest_root/<case.id>``.

    Layout: ``ws/`` holds the skeleton's target contents plus build_recipe.json
    (what the snapshot pins); each commit's edits land in order and the tree
    digest after each stage is recorded as that commit's sha.
    """
    skeleton = SKELETONS / case.skeleton
    target_src = skeleton / "target"
    recipe = skeleton / "build_recipe.json"
    labels = skeleton / "labels.yaml"
    if not target_src.is_dir() or not recipe.is_file() or not labels.is_file():
        raise ValueError(f"skeleton {case.skeleton} incomplete under {skeleton}")

    case_dir = dest_root / case.id
    ws = case_dir / "ws"
    copy_tree(target_src, ws)
    shutil.copy2(recipe, ws / "build_recipe.json")

    recipe_json = json.loads(recipe.read_text(encoding="utf-8"))
    commit_shas: list[str] = []
    applied = 0
    for commit in case.commits:
        for edit in commit.edits:
            _apply_edit(ws, edit)
            applied += 1
        for rel, content in commit.extra_files:
            target = (ws / rel).resolve()
            if ws.resolve() not in target.parents:
                raise ValueError(f"extra_file path escapes workspace: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            applied += 1
        commit_shas.append(tree_digest(ws)[:40])

    return Materialized(
        case=case,
        workspace=ws,
        labels_path=labels,
        target_dir_name=str(recipe_json.get("target_dir") or ""),
        commit_shas=commit_shas,
        applied_edits=applied,
    )


def manifest_for(case: Case, mat: Materialized) -> dict:
    """The reproducibility record kept beside results for one case."""
    return {
        "case_id": case.id,
        "kind": case.kind,
        "skeleton": case.skeleton,
        "structure": case.structure,
        "commit_shas": mat.commit_shas,
        "final_snapshot_sha": mat.commit_shas[-1] if mat.commit_shas else None,
        "commit_messages": [c.message for c in case.commits],
        "n_edits": mat.applied_edits,
    }
