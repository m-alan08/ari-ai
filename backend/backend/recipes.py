"""Build-recipe generator: propose a `build_recipe.json` to every repository connected via the App.

Probe, Patch and Verify need a recipe to start the target app in the sandbox and test it
(`build_recipe.json` at the repository root; see the one for this repository and
`examples/payments-lite/build_recipe.json`). When a repository is connected, the
`propose_build_recipe` job reads its default branch from the bare checkout, indexes it with
code intelligence, and drafts one:

* deterministic: the FastAPI app or app factory, routes with a single path parameter (and
  their mount prefixes), the test layout and exact dependency pins from lock files;
* Claude (the `recipe` agent through the LLM gateway), only for what code reading cannot
  settle: which routes are ownership-scoped, the two fixture identities and their resources,
  and a `deploy/sandbox_target.py` harness that seeds them;
* validated deterministically before anything leaves: JSON round trip, `sandbox_setup`,
  `app_factory`, every route present in the index, identities owning every route's resource,
  the harness parsed (never executed here) and defining the factory.

The draft arrives as a pull request on `ari/build-recipe`; nothing is pushed to the default
branch, and a repository that already has a recipe is left alone. Dry run:

    uv run python -m backend.recipes <local git checkout> [--sha SHA] [--live]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import sqlalchemy as sa
from pydantic import BaseModel, Field, model_validator

from backend import audit, jobs
from backend.checkouts import CheckoutError, _git, _locked, auth_env, fetch_commit
from backend.connection_guard import RepositoryDetached, github_write
from backend.db import repositories, transaction
from backend.services import project_org
from backend.settings import get_settings
from backend.worker import JobContext, PermanentJobError, handler
from shared.audit import ActorType
from shared.errors import ErrorCode

RECIPE_PATH = "build_recipe.json"
HARNESS_PATH = "deploy/sandbox_target.py"
HARNESS_MODULE = "deploy.sandbox_target"
BRANCH = "ari/build-recipe"
# Installed in every microVM by the sandbox driver (vercel_driver.RUNNER_WHEELS); a second
# pin from the recipe would fight the runner's.
RUNNER_PACKAGES = frozenset({"pytest", "httpx", "fastapi", "uvicorn", "pydantic"})
_USERISH = re.compile(
    r"\b\w*(user|owner|current|customer|account|principal|member|tenant|session|auth)\w*\b",
    re.I,
)
_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_REQ = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[A-Za-z0-9,._ -]+\])?\s*(==\s*([^\s;,]+))?"
)
_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# --------------------------------------------------------------------------- inspection


@dataclass
class Facts:
    """What the deterministic pass learned about one repository at one commit."""

    files: dict[str, str]  # indexed text files: path -> content
    app: dict[str, Any] | None = None  # {path, module, attr, factory, source_root}
    routes: list[dict[str, Any]] = field(default_factory=list)
    test_roots: list[str] = field(default_factory=list)
    pins: list[str] = field(default_factory=list)
    unpinned: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def route_keys(self) -> set[tuple[str, str]]:
        return {(r["method"], r["path_template"]) for r in self.routes}


def _source_root(path: str, files: dict[str, str]) -> str:
    """The sys.path entry a module at `path` is imported from: the first ancestor that is
    not itself a package."""
    parent = PurePosixPath(path).parent
    while str(parent) != "." and f"{parent}/__init__.py" in files:
        parent = parent.parent
    return "" if str(parent) == "." else str(parent)


def _module_of(path: str, files: dict[str, str]) -> str:
    parts = list(
        PurePosixPath(path).relative_to(_source_root(path, files) or ".").with_suffix("").parts
    )
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _find_apps(
    files: dict[str, str], trees: dict[str, ast.Module], tests: set[str]
) -> list[tuple[int, dict[str, Any]]]:
    """(rank, app) for every FastAPI app factory (rank 0-1) or module-level app (rank 2)."""
    found: list[tuple[int, dict[str, Any]]] = []
    for path, tree in trees.items():
        if path in tests:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and any(
                isinstance(c, ast.Call) and ast.unparse(c.func).split(".")[-1] == "FastAPI"
                for c in ast.walk(node)
            ):
                a = node.args
                no_required = len(a.defaults) == len(a.args) and all(
                    d is not None for d in a.kw_defaults
                )
                if no_required:
                    rank = 0 if node.name == "create_app" else 1
                    found.append((rank, {"path": path, "attr": node.name, "factory": True}))
            elif (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and ast.unparse(node.value.func).split(".")[-1] == "FastAPI"
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                found.append((2, {"path": path, "attr": node.targets[0].id, "factory": False}))
    return [
        (
            rank,
            app
            | {
                "module": _module_of(app["path"], files),
                "source_root": _source_root(app["path"], files),
            },
        )
        for rank, app in found
    ]


def _imports(tree: ast.Module) -> dict[str, str]:
    """Local name -> dotted module or object it was imported as."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
        elif isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
    return out


def _mount_prefixes(trees: dict[str, ast.Module]) -> dict[str, str]:
    """Module -> prefix its router is included under (`include_router(x.router, prefix=...)`,
    also over a `for r in (a.router, b.router)` loop).

    shortcut: one router per module and one level of include_router; nested routers get no
    prefix, so their routes fail the index check and are left out of the recipe."""
    mounted: dict[str, str] = {}
    for tree in trees.values():
        names = _imports(tree)
        loops = {
            n.target.id: list(n.iter.elts)
            for n in ast.walk(tree)
            if isinstance(n, ast.For)
            and isinstance(n.target, ast.Name)
            and isinstance(n.iter, (ast.Tuple, ast.List))
        }
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "include_router"
                and call.args
            ):
                continue
            prefix = next(
                (
                    k.value.value
                    for k in call.keywords
                    if k.arg == "prefix" and isinstance(k.value, ast.Constant)
                ),
                "",
            )
            arg = call.args[0]
            targets = loops.get(arg.id, [arg]) if isinstance(arg, ast.Name) else [arg]
            for t in targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name):
                    module = names.get(t.value.id, t.value.id)
                elif isinstance(t, ast.Name) and t.id in names:
                    module = names[t.id].rsplit(".", 1)[0]
                else:
                    continue
                if isinstance(prefix, str):
                    mounted[module] = prefix
    return mounted


def _router_prefix(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] == "APIRouter":
            for k in node.keywords:
                if k.arg == "prefix" and isinstance(k.value, ast.Constant):
                    return str(k.value.value)
    return ""


def _ownership_signal(node: ast.AST | None) -> bool:
    """Heuristic: the handler resolves a user-ish principal (call, comparison or dependency)."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    return any(
        isinstance(n, (ast.Call, ast.Compare)) and _USERISH.search(ast.unparse(n))
        for n in ast.walk(node)
    ) or any(_USERISH.search(a.arg) for a in node.args.args)


def read_files(repo_dir: Path, sha: str) -> dict[str, str]:
    from code_intelligence.ingest import GitReader

    _, files, _ = GitReader(repo_dir).read(sha)
    return {f.path: f.content for f in files}


def inspect(repo_dir: Path, sha: str) -> Facts:
    """Index the tree at `sha` (code intelligence) and derive the recipe facts from it."""
    from code_intelligence.extract import PythonExtractor
    from code_intelligence.ingest import GitReader
    from shared.ids import IdPrefix, new_id
    from shared.intelligence import SnapshotRef

    _, sources, _ = GitReader(repo_dir).read(sha)
    files = {f.path: f.content for f in sources}
    facts = Facts(files=files)
    ref = SnapshotRef(
        project_id=new_id(IdPrefix.prj),
        repo_id=new_id(IdPrefix.rep),
        snapshot_id=new_id(IdPrefix.snp),
        commit_sha=sha,
        index_version="recipe",
    )
    index = PythonExtractor(ref, sources)
    index.extract()
    tests = {f.path for f in sources if f.is_test}
    # The index skips files whose redacted text no longer parses; the stdlib parser over the
    # raw blob (parsed here only, never sent anywhere) is enough to find the app and prefixes.
    trees: dict[str, ast.Module] = {}
    for path, content in files.items():
        if path.endswith(".py"):
            for text in (content, _blob(repo_dir, sha, path)):
                try:
                    trees[path] = ast.parse(text or "")
                    break
                except (SyntaxError, ValueError, RecursionError):
                    continue
    apps = _find_apps(files, trees, tests)
    if not apps:
        return facts

    mounted = _mount_prefixes(trees)
    symbols = {s.id: s for s in index.symbols}
    routes: list[dict[str, Any]] = []
    for e in index.entries:
        sym = symbols.get(e.handler_symbol_id)
        if sym is None or sym.path in tests or not e.path_template or not e.method:
            continue
        module = _module_of(sym.path, files)
        full = (
            mounted.get(module, "")
            + _router_prefix(trees.get(sym.path, ast.Module(body=[], type_ignores=[])))
            + e.path_template
        )
        routes.append(
            {
                "method": e.method,
                "path_template": full,
                "handler": sym.qualified_name,
                "file": sym.path,
                "line": sym.start_line,
                "auth_dependencies": e.auth_dependencies,
                "ownership_signal": bool(e.auth_dependencies)
                or _ownership_signal(index.nodes.get(sym.id)),
            }
        )

    def served(app: dict[str, Any]) -> list[dict[str, Any]]:
        root = app["source_root"]
        return [r for r in routes if not root or r["file"].startswith(root + "/")]

    # In a monorepo the app serving the most indexed routes is the product; examples lose.
    _, facts.app = min(
        apps, key=lambda a: (-len(served(a[1])), a[0], a[1]["path"].count("/"), a[1]["path"])
    )
    facts.routes = served(facts.app)
    facts.test_roots = _test_roots(files, tests, facts.app["source_root"])
    facts.pins, facts.unpinned = _pins(repo_dir, sha, files, facts.app["source_root"])
    return facts


def _test_roots(files: dict[str, str], tests: set[str], source_root: str) -> list[str]:
    declared: list[str] = []
    try:
        ini = tomllib.loads(files.get("pyproject.toml", ""))["tool"]["pytest"]["ini_options"]
        declared = [str(p).strip("/") for p in ini.get("testpaths", []) if isinstance(p, str)]
    except (KeyError, tomllib.TOMLDecodeError, TypeError):
        pass
    counts: dict[str, int] = {}
    for path in tests:
        name = PurePosixPath(path).name
        if not (name.startswith("test_") or name.endswith("_test.py")):
            continue
        parts = PurePosixPath(path).parts
        cut = next((i for i, p in enumerate(parts) if p in ("tests", "test")), len(parts) - 2)
        root = "/".join(parts[: cut + 1])
        counts[root] = counts.get(root, 0) + 1
    # The app's own tests first: in a monorepo, test_command runs only the first root.
    near = source_root + "/" if source_root else ""
    found = sorted(counts, key=lambda r: (-counts[r], r))
    roots = [r for r in declared if r in counts or any(t.startswith(r + "/") for t in tests)]
    return sorted(dict.fromkeys(roots + found), key=lambda r: not r.startswith(near))[:4]


def _blob(repo_dir: Path, sha: str, path: str) -> str | None:
    try:
        return _git("-C", str(repo_dir), "show", f"{sha}:{path}", timeout=30)
    except CheckoutError:
        return None


def _pins(
    repo_dir: Path, sha: str, files: dict[str, str], source_root: str
) -> tuple[list[str], list[str]]:
    """Exact `name[extras]==version` for the app's direct dependencies, from lock files or
    `==` requirements. Returns (pins, names that could not be pinned)."""
    wanted: dict[str, tuple[str, str, str | None]] = {}  # norm -> (name, extras, pin)

    def add(spec: str) -> None:
        m = _REQ.match(spec)
        if m and _norm(m.group(1)) not in RUNNER_PACKAGES:
            wanted.setdefault(_norm(m.group(1)), (m.group(1), m.group(2) or "", m.group(4)))

    for pyproject in dict.fromkeys(["pyproject.toml", f"{source_root}/pyproject.toml".lstrip("/")]):
        try:
            data = tomllib.loads(files.get(pyproject, ""))
        except tomllib.TOMLDecodeError:
            continue
        for spec in data.get("project", {}).get("dependencies", []):
            add(str(spec))
        for name in data.get("tool", {}).get("poetry", {}).get("dependencies", {}):
            if name != "python":
                add(name)
    for path, content in files.items():
        if PurePosixPath(path).parent == PurePosixPath(source_root or ".") or "/" not in path:
            if (
                re.fullmatch(r"requirements[\w.-]*\.txt", PurePosixPath(path).name)
                and "dev" not in path
            ):
                for line in content.splitlines():
                    if not line.strip().startswith(("#", "-")):
                        add(line)
    locked: dict[str, str] = {}
    local: set[str] = set()  # workspace members and path dependencies: uploaded, not installed
    for lock in ("uv.lock", "poetry.lock"):
        try:
            for pkg in tomllib.loads(_blob(repo_dir, sha, lock) or "").get("package", []):
                name, source = _norm(str(pkg.get("name", ""))), pkg.get("source") or {}
                if lock == "uv.lock" and "registry" not in source:
                    local.add(name)
                elif source.get("type") in ("directory", "file", "git"):
                    local.add(name)
                else:
                    locked.setdefault(name, str(pkg.get("version", "")))
        except tomllib.TOMLDecodeError:
            continue
    pins, unpinned = [], []
    for norm, (name, extras, pin) in sorted(wanted.items()):
        version = pin or locked.get(norm)
        if version:
            pins.append(f"{name}{extras.replace(' ', '')}=={version}")
        elif norm not in local:
            unpinned.append(name)
    return pins[:60], unpinned


# --------------------------------------------------------------------------- drafting


@dataclass
class Draft:
    recipe: dict[str, Any]
    harness: str | None
    author: str  # "heuristic" or "claude"
    notes: list[str]

    def files(self) -> dict[str, str]:
        out = {RECIPE_PATH: json.dumps(self.recipe, indent=2) + "\n"}
        if self.harness:
            out[HARNESS_PATH] = self.harness.rstrip("\n") + "\n"
        return out


def _wrapper_harness(app: dict[str, Any]) -> str:
    """A factory over a module-level app, or over one outside the repository root."""
    path_line = (
        f'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "{app["source_root"]}"))\n'
        if app["source_root"]
        else ""
    )
    call = "()" if app["factory"] else ""
    return (
        '"""Sandbox target for Ari\'s security agents: build_recipe.json names\n'
        f"`{HARNESS_MODULE}:create_app`. Generated by Ari; never imported by the app itself.\n"
        '"""\n\n'
        "import sys\nfrom pathlib import Path\n\n" + path_line + "\n\n"
        "def create_app():  # type: ignore[no-untyped-def]\n"
        f"    from {app['module']} import {app['attr']}\n\n"
        f"    return {app['attr']}{call}\n"
    )


def base_recipe(facts: Facts) -> Draft:
    """The deterministic recipe: enough for Patch and Verify to run the tests."""
    assert facts.app is not None
    app = facts.app
    direct = app["factory"] and not app["source_root"]
    recipe: dict[str, Any] = {
        "_comment": [
            "Sandbox build recipe drafted by Ari from this repository's code. The security",
            "agents start `app_factory` inside an isolated microVM; Probe and Verify may only",
            "name the identities, resources and routes below. Review before merging.",
        ],
        "recipe_version": 1,
        "kind": "python_asgi",
        "app_factory": f"{app['module']}:{app['attr']}"
        if direct
        else f"{HARNESS_MODULE}:create_app",
        "test_command": ["-q", *facts.test_roots[:1]] if facts.test_roots else ["-q"],
        "test_roots": facts.test_roots or ["tests"],
        "sandbox": {"apt": [], "pip": facts.pins, "include": []},
        "identities": {},
        "resources": {},
        "routes": [],
    }
    notes = list(facts.notes)
    if facts.unpinned:
        notes.append(
            "No exact version found for: " + ", ".join(facts.unpinned) + ". Add `name==version` "
            "pins to `sandbox.pip` (or commit a lock file) so the sandbox can install them."
        )
    return Draft(recipe, None if direct else _wrapper_harness(app), "heuristic", notes)


def _mentioned(value: str, harness: str | None, facts: Facts) -> bool:
    """Fixture values must be literal in the harness (or, without one, in the app's code):
    a token or id nothing seeds would make every check fail for the wrong reason."""
    sources = [harness] if harness else [c for p, c in facts.files.items() if p.endswith(".py")]
    return any(value in s for s in sources)


def problems(recipe: dict[str, Any], harness: str | None, facts: Facts) -> list[str]:
    """Deterministic validation; an empty list means the recipe may be proposed."""
    from security_agents.checks.library import app_factory
    from security_agents.contracts import PlatformError
    from security_agents.tools.services import sandbox_setup

    out: list[str] = []
    try:
        recipe = json.loads(json.dumps(recipe))
    except (TypeError, ValueError) as exc:
        return [f"recipe is not JSON: {exc}"]
    if recipe.get("recipe_version") != 1 or recipe.get("kind") != "python_asgi":
        out.append("recipe_version must be 1 and kind python_asgi")
    try:
        sandbox_setup(recipe)
        module, attr = app_factory(recipe)
    except PlatformError as exc:
        return [*out, exc.message]
    if module == HARNESS_MODULE:
        if not harness:
            out.append(f"app_factory names {HARNESS_PATH} but no harness was provided")
        else:
            try:
                tree = ast.parse(harness)
            except SyntaxError as exc:
                return [*out, f"harness does not parse: {exc}"]
            if not any(isinstance(n, ast.FunctionDef) and n.name == attr for n in tree.body):
                out.append(f"harness defines no top-level function {attr}()")
    else:
        if not any(
            p.endswith(".py")
            and _module_of(p, facts.files) == module
            and re.search(rf"^(def {attr}\b|{attr}\s*=)", facts.files[p], re.M)
            for p in facts.files
        ):
            out.append(f"app_factory {module}:{attr} is not defined in the repository")
    roots = recipe.get("test_roots") or []
    for root in roots:
        if not any(p.startswith(f"{root}/") for p in facts.files):
            out.append(f"test root {root!r} has no files")
    identities = recipe.get("identities") or {}
    routes = recipe.get("routes") or []
    if routes and len(identities) < 2:
        out.append("routes need at least two fixture identities")
    for name, ident in identities.items():
        header = ident.get("auth_header") if isinstance(ident, dict) else None
        if (
            not _IDENT.match(str(name))
            or not isinstance(header, str)
            or not 0 < len(header) <= 200
            or "\n" in header
        ):
            out.append(f"identity {name!r} needs a lowercase name and a one-line auth_header")
        elif not _mentioned(header.split()[-1], harness, facts):
            out.append(f"identity {name!r}: its token {header.split()[-1]!r} is never seeded")
    resources = recipe.get("resources") or {}
    seen: set[str] = set()
    index = facts.route_keys()
    for route in routes:
        rid, path = route.get("id"), str(route.get("path_template", ""))
        if not _IDENT.match(str(rid)) or rid in seen:
            out.append(f"route id {rid!r} must be unique snake_case")
        seen.add(str(rid))
        if (str(route.get("method", "")).upper(), path) not in index:
            out.append(f"route {route.get('method')} {path} is not in the code-intelligence index")
        if len(_PARAM.findall(path)) != 1:
            out.append(f"route {rid!r} must have exactly one path parameter")
        owned = resources.get(route.get("resource"), {})
        missing = sorted(
            set(identities) - {k for k, v in owned.items() if isinstance(v, dict) and v.get("id")}
        )
        if missing:
            out.append(
                f"route {rid!r}: resource {route.get('resource')!r} has no id for {', '.join(missing)}"
            )
    for kind, owners in resources.items():
        ids = [str(v.get("id")) for v in owners.values() if isinstance(v, dict)]
        if len(set(ids)) != len(ids):
            out.append(f"resource {kind!r}: each identity must own a different id")
        for rid in ids:
            if not _mentioned(rid, harness, facts):
                out.append(f"resource {kind!r} id {rid!r} is never seeded")
    return out


SYSTEM = """You draft the sandbox build recipe for a Python web application so Ari's \
security agents can start it in an isolated microVM (no network after package install) and \
run ownership checks: identity A must read its own resource on a route, identity B must be \
denied A's resource.

You receive the facts a deterministic pass found (app, routes from the code index, tests, \
dependency pins). Read the code with the tools to learn how requests are authenticated and \
how data is stored, then submit:

- routes: 1-8 GET routes from the given list (exact method and path_template) whose single \
path parameter names a resource that belongs to a user/tenant and that the handler scopes to \
the caller. Give each a snake_case id and a resource kind.
- identities: exactly two unrelated fixture users, e.g. alice and bob, each with the exact \
`Authorization` header value the sandbox will send (e.g. "Bearer sbx-alice-session").
- resources: for every route's resource kind, the id each identity owns.
- harness_source: a Python module saved as deploy/sandbox_target.py defining `create_app()` \
that returns the real ASGI app with the fixture data in place: start/point at a throwaway \
database if the app needs one (SQLite file, or a local PostgreSQL via apt `postgresql` started \
with pg_ctl over a Unix socket), create the schema the way the app does (migrations or \
metadata.create_all), seed both users and their resources with the fixed ids, and make the \
two auth headers authenticate as those users (seed matching sessions/tokens, or an ASGI \
middleware / dependency override that maps the header to the user). Cache the app in a \
module global. Fixed synthetic values only: no real secrets, no network calls. Every token and \
resource id you declare must appear literally in harness_source. Set harness_source to null \
only when the app's own factory already serves seeded fixture users; app_factory is then that \
factory.
- apt/pip: extra packages the harness needs (pip entries must be exact `name==version`).

Your result is validated deterministically; a rejected result comes back with the reasons."""


class ProposedRoute(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    method: str = Field(pattern=r"^GET$")
    path_template: str = Field(max_length=300)
    resource: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")


class Proposal(BaseModel):
    app_factory: str = Field(
        max_length=200, description="module:callable, e.g. deploy.sandbox_target:create_app"
    )
    harness_source: str | None = Field(default=None, max_length=40_000)
    identities: dict[str, str] = Field(description="identity name -> Authorization header value")
    resources: dict[str, dict[str, str]] = Field(
        description="resource kind -> identity -> resource id"
    )
    routes: list[ProposedRoute] = Field(min_length=1, max_length=8)
    apt: list[str] = Field(default_factory=list, max_length=10)
    pip: list[str] = Field(default_factory=list, max_length=20)
    notes: list[str] = Field(default_factory=list, max_length=10)


def merge(base: Draft, p: Proposal) -> Draft:
    recipe = json.loads(json.dumps(base.recipe))
    recipe["app_factory"] = p.app_factory
    recipe["sandbox"]["apt"] = list(dict.fromkeys(p.apt))
    recipe["sandbox"]["pip"] = list(dict.fromkeys([*recipe["sandbox"]["pip"], *p.pip]))[:60]
    recipe["identities"] = {k: {"auth_header": v} for k, v in p.identities.items()}
    recipe["resources"] = {k: {o: {"id": i} for o, i in v.items()} for k, v in p.resources.items()}
    recipe["routes"] = [r.model_dump() for r in p.routes]
    return Draft(recipe, p.harness_source, "claude", [*base.notes, *(n[:300] for n in p.notes)])


def _tools(files: dict[str, str]) -> list[Any]:
    from backend.llm import Tool

    class ListIn(BaseModel):
        prefix: str = ""

    class ReadIn(BaseModel):
        path: str
        start_line: int = Field(default=1, ge=1)
        end_line: int = Field(default=300, ge=1)

    class SearchIn(BaseModel):
        pattern: str = Field(max_length=200, description="Python regular expression")
        prefix: str = ""

    def list_files(a: ListIn) -> list[str]:
        return [p for p in sorted(files) if p.startswith(a.prefix)][:300]

    def read_file(a: ReadIn) -> str:
        if a.path not in files:
            raise ValueError(f"no indexed file {a.path!r}")
        lines = files[a.path].splitlines()[a.start_line - 1 : min(a.end_line, a.start_line + 399)]
        return "\n".join(f"{a.start_line + i}: {line}" for i, line in enumerate(lines))

    def search(a: SearchIn) -> list[str]:
        rx = re.compile(a.pattern)
        hits = [
            f"{p}:{n}: {line.strip()[:200]}"
            for p in sorted(files)
            if p.startswith(a.prefix)
            for n, line in enumerate(files[p].splitlines(), 1)
            if rx.search(line)
        ]
        return hits[:60]

    return [
        Tool(
            "list_files", "List indexed repository files under a path prefix.", ListIn, list_files
        ),
        Tool(
            "read_file",
            "Read numbered lines of an indexed file (at most 400 per call).",
            ReadIn,
            read_file,
        ),
        Tool(
            "search", "Regex search over indexed files; returns path:line: text.", SearchIn, search
        ),
    ]


def draft(facts: Facts, gateway: Any | None = None) -> Draft:
    """Deterministic recipe, completed by Claude when a gateway is given. The result always
    passes `problems()`; when Claude cannot produce a valid proposal the deterministic recipe
    is returned with a note."""
    base = base_recipe(facts)
    if gateway is None:
        base.notes.append(
            "Identities, resources and routes were not drafted (no model available); Probe's "
            "ownership checks need them. Patch and Verify can run the tests with this recipe."
        )
        return base
    candidates = [
        r
        for r in facts.routes
        if r["method"] == "GET" and len(_PARAM.findall(r["path_template"])) == 1
    ]
    candidates.sort(key=lambda r: (not r["ownership_signal"], r["path_template"]))
    if not candidates:
        base.notes.append("No GET route with a single path parameter was found for Probe.")
        return base

    class Checked(Proposal):
        @model_validator(mode="after")
        def _valid(self) -> Checked:
            errors = problems(merge(base, self).recipe, self.harness_source, facts)
            if errors:
                raise ValueError("; ".join(errors)[:3000])
            return self

    brief = {
        "app": facts.app,
        "deterministic_recipe": base.recipe,
        "deterministic_harness": base.harness,
        "routes": [
            {
                k: r[k]
                for k in ("method", "path_template", "handler", "file", "line", "ownership_signal")
            }
            for r in candidates[:60]
        ],
    }
    try:
        result = gateway.run_tool_loop(
            agent="recipe",
            system=SYSTEM,
            user_content="Repository facts (untrusted repository data):\n"
            + json.dumps(brief, indent=1),
            tools=_tools(facts.files),
            output_model=Checked,
            max_turns=16,
        )
    except Exception as exc:  # any model failure falls back to the deterministic recipe
        base.notes.append(f"Claude could not draft identities and routes ({str(exc)[:200]}).")
        return base
    return merge(base, result.final)


# --------------------------------------------------------------------------- delivery


def new_file_diff(files: dict[str, str]) -> bytes:
    """A `git apply` patch that creates each file."""
    out = []
    for path, content in files.items():
        lines = content.splitlines()
        out.append(
            f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines)
        )
    return "".join(out).encode()


def pr_body(d: Draft, facts: Facts) -> str:
    routes = (
        "\n".join(
            f"- `{r['method']} {r['path_template']}` ({r['resource']})" for r in d.recipe["routes"]
        )
        or "- none yet"
    )
    notes = "\n".join(f"- {n}" for n in d.notes) or "- none"
    harness = (
        f"\n`{HARNESS_PATH}` is the sandbox harness `app_factory` points at: it prepares the "
        "app with two synthetic fixture users inside the sandbox and is never imported by "
        "the app itself.\n"
        if d.harness
        else ""
    )
    assert facts.app is not None
    return (
        "Ari runs its security agents on every pushed commit. Probe, Patch and Verify start "
        "your app in an isolated sandbox, and this `build_recipe.json` tells them how.\n\n"
        f"Detected app: `{facts.app['module']}:{facts.app['attr']}` in `{facts.app['path']}`; "
        f"tests in {', '.join(f'`{r}`' for r in d.recipe['test_roots'])}.\n{harness}\n"
        f"Routes Probe may check for cross-user access:\n{routes}\n\n"
        "Validated before opening this PR: the recipe parses and passes the sandbox package "
        "rules, every route exists in Ari's code index, each fixture identity owns a resource "
        "for every route, and the harness parses. It has not run yet: the first assessment "
        f"after merging proves it.\n\nNotes:\n{notes}\n\n"
        f"Drafted {'with Claude' if d.author == 'claude' else 'from code analysis'}. "
        "Edit freely before merging; close the PR to opt out."
    )


def _default_sha(full_name: str, branch: str, token: str, web: str) -> str:
    out = _git(
        "ls-remote", f"{web.rstrip('/')}/{full_name}.git", f"refs/heads/{branch}",
        extra_env=auth_env(token),
    ).split()  # fmt: skip
    if not out:
        raise CheckoutError(f"{full_name} has no branch {branch}")
    return out[0]


def gateway_or_none() -> Any | None:
    from backend.llm import build_gateway, llm_mode

    return build_gateway() if llm_mode() != "unavailable" else None


@handler("propose_build_recipe")
def propose_build_recipe(ctx: JobContext) -> dict[str, Any]:
    ctx.check_cancelled()
    s = get_settings()
    with transaction() as conn:
        repo = (
            conn.execute(
                sa.select(repositories).where(repositories.c.id == ctx.job.payload["repo_id"])
            )
            .mappings()
            .first()
        )
        if repo is None or repo["installation_id"] is None or repo["disconnected_at"] is not None:
            return {
                "status": "skipped",
                "reason": "repository is not connected through the GitHub App",
            }
        org_id = project_org(conn, repo["project_id"])
    if s.security_workdir is None:
        raise PermanentJobError(ErrorCode.SUBSYSTEM_UNAVAILABLE, "SECURITY_WORKDIR is not set")

    def finish(action: str, **details: Any) -> dict[str, Any]:
        with transaction() as conn:
            audit.record(
                conn, org_id=org_id, project_id=repo["project_id"], actor_type=ActorType.system,
                actor_id="recipe-generator", action=action, subject_type="repository",
                subject_id=repo["id"], details=details,
            )  # fmt: skip
        return {"status": action.split(".", 1)[1], **details}

    from backend.github import GitHubApp
    from code_intelligence.models import IntelligenceError

    full_name, branch, iid = (
        str(repo["full_name"]),
        str(repo["default_branch"]),
        int(repo["installation_id"]),
    )
    repo_dir = s.security_workdir / "repos" / f"{repo['id']}.git"
    try:
        app = GitHubApp(settings=s)
        token = app.installation_token(iid)
        sha = _default_sha(full_name, branch, token, s.github_web_url)
        with _locked(repo_dir):
            fetch_commit(repo_dir, full_name, sha, token, github_web_url=s.github_web_url)
            if _blob(repo_dir, sha, RECIPE_PATH) is not None:
                return finish(
                    "recipe.skipped", reason="repository already has a build recipe", commit_sha=sha
                )
            facts = inspect(repo_dir, sha)
            if facts.app is None:
                return finish("recipe.unsupported", reason="no FastAPI app found", commit_sha=sha)
            if _blob(repo_dir, sha, HARNESS_PATH) is not None:
                return finish(
                    "recipe.unsupported", reason=f"{HARNESS_PATH} already exists", commit_sha=sha
                )
        d = draft(facts, gateway_or_none())  # outside the lock: Claude may take a minute
        if errors := problems(d.recipe, d.harness, facts):  # never propose an invalid recipe
            return finish("recipe.invalid", reasons=errors[:10], commit_sha=sha)
        from backend.delivery import push_patch

        # Drafting can take a minute; the owner may have disconnected, or GitHub detached the
        # installation, since the check at the top. The write lease re-checks before the
        # write token is minted and before every write that uses it.
        with github_write(str(repo["id"]), iid):
            write = app.installation_token(iid, write=True, repository=full_name.split("/", 1)[1])
        message = (
            "chore: add Ari sandbox build recipe\n\nDrafted by Ari from the repository's code."
        )
        with _locked(repo_dir):
            with github_write(str(repo["id"]), iid):
                push_patch(
                    repo_dir, full_name, sha, new_file_diff(d.files()), write, message, BRANCH,
                    github_web_url=s.github_web_url,
                )  # fmt: skip
        with github_write(str(repo["id"]), iid):
            url = app.open_pull_request(
                iid, full_name, head=BRANCH, base=branch,
                title="Add Ari sandbox build recipe", body=pr_body(d, facts),
            )  # fmt: skip
    except RepositoryDetached as exc:
        return finish("recipe.skipped", reason=exc.reason, commit_sha=sha)
    except (CheckoutError, IntelligenceError, httpx.HTTPError) as exc:  # too big, refused, gone
        from backend.delivery import _transient

        if isinstance(exc, httpx.HTTPError) and _transient(exc):
            raise  # the queue retries with backoff; the branch and PR steps are idempotent
        # GitHub needs a moment to make a just-created repo visible to App tokens; a
        # git/network flap is transient, so let the queue retry while attempts remain.
        # On the last attempt fall through and record the terminal recipe.failed.
        if isinstance(exc, CheckoutError) and ctx.job.attempts < ctx.job.max_attempts:
            raise
        return finish("recipe.failed", reason=str(exc)[:300])
    return finish(
        "recipe.pr_opened", url=url, branch=BRANCH, commit_sha=sha, author=d.author,
        routes=[r["id"] for r in d.recipe["routes"]],
    )  # fmt: skip


def enqueue(conn: sa.Connection, repo_id: str, reason: str) -> None:
    jobs.enqueue(
        conn, "propose_build_recipe", {"repo_id": repo_id}, dedupe_key=f"recipe:{repo_id}:{reason}"
    )


def enqueue_for_installation(
    conn: sa.Connection, event: str, payload: dict[str, Any], delivery_id: str
) -> list[str]:
    """`installation` (created / unsuspended) and `installation_repositories` (added): propose
    a recipe to each listed repository that is already connected. Returns their ids."""
    listed = (
        payload.get("repositories")
        if event == "installation"
        else payload.get("repositories_added")
    )
    names = [
        r["full_name"]
        for r in listed or []
        if isinstance(r, dict) and isinstance(r.get("full_name"), str)
    ]
    if not names or payload.get("action") not in ("created", "unsuspend", "added"):
        return []
    ids = list(
        conn.execute(
            sa.select(repositories.c.id).where(
                repositories.c.full_name.in_(names),
                repositories.c.installation_id.is_not(None),
                repositories.c.disconnected_at.is_(None),
            )
        ).scalars()
    )
    for rid in ids:
        enqueue(conn, rid, delivery_id)
    return ids


def main(argv: list[str] | None = None) -> int:
    """Dry run: print the recipe, harness and PR Ari would open for a local checkout."""
    ap = argparse.ArgumentParser(prog="python -m backend.recipes", description=main.__doc__)
    ap.add_argument("repo", type=Path)
    ap.add_argument("--sha", help="commit to inspect (default HEAD)")
    ap.add_argument("--live", action="store_true", help="let Claude draft routes (costs money)")
    args = ap.parse_args(argv)
    sha = args.sha or _git("-C", str(args.repo), "rev-parse", "HEAD").strip()
    if _blob(args.repo, sha, RECIPE_PATH) is not None:
        print(f"skip: {RECIPE_PATH} already exists at {sha[:12]}")
        return 0
    facts = inspect(args.repo, sha)
    if facts.app is None:
        print("unsupported: no FastAPI app found")
        return 1
    d = draft(facts, gateway_or_none() if args.live else None)
    errors = problems(d.recipe, d.harness, facts)
    for path, content in d.files().items():
        print(f"===== {path}\n{content}")
    print(f"===== PR {BRANCH} -> default branch: Add Ari sandbox build recipe\n{pr_body(d, facts)}")
    print("===== validation: " + ("ok" if not errors else "; ".join(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
