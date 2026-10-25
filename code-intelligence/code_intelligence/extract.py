"""Deterministic Python structural extraction; dynamic resolution stays explicitly uncertain."""

from __future__ import annotations

import ast
import tomllib
from collections import defaultdict
from collections.abc import Iterator
from functools import cached_property
from pathlib import PurePosixPath

import tree_sitter_python
from shared.evidence import EvidenceKind, EvidenceRef, SourceSpan
from shared.intelligence import (
    BehavioralExpectation,
    CallEdge,
    CallResolution,
    DataSensitivity,
    EntryPoint,
    EntryPointKind,
    ExpectationKind,
    ExpectationOrigin,
    ExpectationStatus,
    SnapshotRef,
    Symbol,
    SymbolKind,
)
from tree_sitter import Language, Parser

from code_intelligence.models import (
    DependencyRecord,
    ImportRecord,
    IndexIssue,
    SourceFile,
    stable_id,
)

Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if "src" in parts:
        parts = parts[len(parts) - parts[::-1].index("src") :]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or "__init__"


def citation(
    ref: SnapshotRef,
    symbol: Symbol,
    *,
    start: int | None = None,
    end: int | None = None,
    note: str | None = None,
) -> EvidenceRef:
    return EvidenceRef(
        kind=EvidenceKind.source_span,
        path=symbol.path,
        commit_sha=ref.commit_sha,
        span=SourceSpan(start_line=start or symbol.start_line, end_line=end or symbol.end_line),
        symbol=symbol.qualified_name,
        provenance=f"code_intelligence@{ref.index_version}",
        note=note,
    )


class PythonExtractor:
    def __init__(self, ref: SnapshotRef, files: list[SourceFile]) -> None:
        self.ref = ref
        self.files = files
        self.symbols: list[Symbol] = []
        self.imports: list[ImportRecord] = []
        self.calls: list[CallEdge] = []
        self.entries: list[EntryPoint] = []
        self.expectations: list[BehavioralExpectation] = []
        self.issues: list[IndexIssue] = []
        self.trees: dict[str, ast.Module] = {}
        self.nodes: dict[str, Definition | ast.Module] = {}
        self.owners: dict[int, Symbol] = {}
        self.by_name: dict[str, list[Symbol]] = defaultdict(list)
        self.shadowed: dict[str, set[str]] = defaultdict(set)
        self.parameter_types: dict[str, dict[str, str]] = defaultdict(dict)
        self.parser = Parser(Language(tree_sitter_python.language()))
        package_dirs = {
            str(PurePosixPath(f.path).parent)
            for f in files
            if PurePosixPath(f.path).name == "__init__.py"
        }
        self.modules: dict[str, str] = {}
        for file in files:
            pure = PurePosixPath(file.path)
            parts = [] if pure.stem == "__init__" else [pure.stem]
            parent = pure.parent
            while str(parent) in package_dirs:
                parts.insert(0, parent.name)
                parent = parent.parent
            self.modules[file.path] = (
                ".".join(parts) if str(pure.parent) in package_dirs else module_name(file.path)
            )

    def _module(self, path: str) -> str:
        return self.modules[path]

    def extract(self) -> None:
        for file in self.files:
            if file.language != "python":
                continue
            structural = self.parser.parse(file.content.encode())
            if structural.root_node.has_error:
                self.issues.append(IndexIssue(path=file.path, reason="Tree-sitter parse error"))
                continue
            try:
                tree = ast.parse(file.content, filename=file.path)
            except (SyntaxError, ValueError, RecursionError):
                self.issues.append(IndexIssue(path=file.path, reason="Python AST parse error"))
                continue
            self.trees[file.path] = tree
            module = self._symbol(file, tree, self._module(file.path), "module")
            self._walk_definitions(file, tree, module)
        for file in self.files:
            if file.path in self.trees:
                self._walk_uses(file, self.trees[file.path])
        for file in self.files:
            if file.path in self.trees:
                self._derive(file, self.trees[file.path])
        self._link_http_tests()
        self._document_expectations()
        self._route_ownership_expectations()
        self.calls = list(
            {
                (e.caller_id, e.callee_id, e.callee_name, e.resolution): e for e in self.calls
            }.values()
        )

    def _symbol(self, file: SourceFile, node: ast.AST, name: str, kind: str) -> Symbol:
        decorators = getattr(node, "decorator_list", [])
        start = min([getattr(node, "lineno", 1)] + [d.lineno for d in decorators])
        end = getattr(node, "end_lineno", None) or max(1, len(file.content.splitlines()))
        signature = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            signature = (
                f"{node.name}({ast.unparse(node.args)})"
                + (f" -> {ast.unparse(node.returns)}" if node.returns else "")
            )[:500]
        symbol = Symbol(
            id=stable_id("sym", self.ref.snapshot_id, file.path, name, str(start)),
            snapshot_id=self.ref.snapshot_id,
            kind=SymbolKind(kind),
            qualified_name=name[:500],
            path=file.path,
            start_line=start,
            end_line=end,
            signature=signature,
            decorators=[ast.unparse(d)[:500] for d in decorators],
            docstring=(ast.get_docstring(node) or "")[:20_000] or None
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            else None,
        )
        self.symbols.append(symbol)
        self.nodes[symbol.id] = node  # type: ignore[assignment]
        self.by_name[symbol.qualified_name].append(symbol)
        self.owners[id(node)] = symbol
        return symbol

    def _walk_definitions(self, file: SourceFile, node: ast.AST, owner: Symbol) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = (
                    "class"
                    if isinstance(child, ast.ClassDef)
                    else ("method" if owner.kind.value == "class" else "function")
                )
                symbol = self._symbol(file, child, f"{owner.qualified_name}.{child.name}", kind)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = child.args
                    self.shadowed[symbol.id].update(
                        a.arg
                        for a in [
                            *args.posonlyargs,
                            *args.args,
                            *args.kwonlyargs,
                            *([args.vararg] if args.vararg else []),
                            *([args.kwarg] if args.kwarg else []),
                        ]
                    )
                    self.parameter_types[symbol.id].update(
                        {
                            a.arg: ast.unparse(a.annotation).strip("\"'")
                            for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]
                            if a.annotation is not None
                        }
                    )
                self._walk_definitions(file, child, symbol)
            else:
                self._walk_definitions(file, child, owner)

    def _walk_uses(self, file: SourceFile, tree: ast.Module) -> None:
        def visit(node: ast.AST, owner: Symbol) -> None:
            if id(node) in self.owners:
                owner = self.owners[id(node)]
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                self.shadowed[owner.id].add(node.id)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = ""
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if node.level:
                        package = self._module(file.path).split(".")
                        if PurePosixPath(file.path).name != "__init__.py":
                            package.pop()
                        if node.level > len(package):
                            # Preserve the unresolved relative spelling rather than
                            # turning an invalid import into an unrelated root module.
                            module = "." * node.level + module
                            self.issues.append(
                                IndexIssue(
                                    path=file.path,
                                    reason=(
                                        f"Relative import at line {node.lineno} "
                                        "exceeds known package depth"
                                    ),
                                )
                            )
                        else:
                            prefix = package[: len(package) - node.level + 1]
                            module = ".".join([*prefix, *([module] if module else [])])
                for alias in node.names:
                    self.imports.append(
                        ImportRecord(
                            path=file.path,
                            line=node.lineno,
                            module=module or alias.name,
                            name=alias.name if isinstance(node, ast.ImportFrom) else None,
                            alias=alias.asname,
                            scope=owner.qualified_name,
                        )
                    )
            for child in ast.iter_child_nodes(node):
                visit(child, owner)

        visit(tree, self.owners[id(tree)])

    @cached_property
    def _imports_in(self) -> dict[tuple[str, str], list[ImportRecord]]:
        """Imports by (scope, path); built on first resolve, after _walk_uses collected them all."""
        index: dict[tuple[str, str], list[ImportRecord]] = {}
        for i in self.imports:
            index.setdefault((i.scope, i.path), []).append(i)
        return index

    def resolve(self, name: str, owner: Symbol) -> tuple[Symbol | None, str]:
        parts = name.split(".")
        if parts[0] in {"self", "cls"} and len(parts) == 2:
            cls = owner.qualified_name.rsplit(".", 1)[0]
            targets = self.by_name.get(f"{cls}.{parts[1]}", [])
            if len(targets) == 1:
                return targets[0], "likely"
        if len(parts) == 2 and parts[0] in self.parameter_types[owner.id]:
            class_name = self.parameter_types[owner.id][parts[0]]
            if class_name != parts[0]:
                type_symbol, _ = self.resolve(class_name, owner)
                targets = (
                    self.by_name.get(f"{type_symbol.qualified_name}.{parts[1]}", [])
                    if type_symbol
                    else []
                )
                if len(targets) == 1:
                    return targets[0], "likely"
        if parts[0] in self.shadowed[owner.id]:
            return None, "unresolved"
        # Lexical resolution walks enclosing functions/classes then the module.
        scope = owner.qualified_name
        while scope:
            candidates = self.by_name.get(f"{scope}.{name}", [])
            if len(candidates) == 1:
                return candidates[0], "exact"
            bindings = [
                i
                for i in self._imports_in.get((scope, owner.path), ())
                if (i.alias or i.name or i.module.split(".")[0]) == parts[0]
            ]
            if len(bindings) == 1:
                imp = bindings[0]
                target = f"{imp.module}.{imp.name}" if imp.name else imp.module
                if not imp.name and not imp.alias:
                    target = imp.module.split(".")[0]
                target = ".".join([target, *parts[1:]])
                candidates = self.by_name.get(target, [])
                if len(candidates) == 1:
                    return candidates[0], "exact"
                return None, "unresolved"
            if "." not in scope:
                break
            scope = scope.rsplit(".", 1)[0]
        return None, "unresolved"

    def _derive(self, file: SourceFile, tree: ast.Module) -> None:
        def visit(node: ast.AST, owner: Symbol) -> None:
            if id(node) in self.owners:
                owner = self.owners[id(node)]
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)[:500]
                target, resolution = self.resolve(name, owner)
                self.calls.append(
                    CallEdge(
                        snapshot_id=self.ref.snapshot_id,
                        caller_id=owner.id,
                        callee_id=target.id if target else None,
                        callee_name=name,
                        resolution=CallResolution(resolution),
                    )
                )
                # Depends is a declared dependency, not an executed runtime observation.
                if name.split(".")[-1] in {"Depends", "Security"} and node.args:
                    dep = ast.unparse(node.args[0])[:500]
                    target, resolution = self.resolve(dep, owner)
                    self.calls.append(
                        CallEdge(
                            snapshot_id=self.ref.snapshot_id,
                            caller_id=owner.id,
                            callee_id=target.id if target else None,
                            callee_name=dep,
                            resolution=CallResolution(resolution),
                        )
                    )
            for child in ast.iter_child_nodes(node):
                visit(child, owner)

        visit(tree, self.owners[id(tree)])
        for symbol in [s for s in self.symbols if s.path == file.path]:
            node = self.nodes[symbol.id]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._routes(symbol, node)
            self._expectations(file, symbol, node)

    def _routes(self, symbol: Symbol, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        auth: list[str] = []
        for candidate in ast.walk(node.args):
            if (
                isinstance(candidate, ast.Call)
                and ast.unparse(candidate.func).split(".")[-1]
                in {
                    "Depends",
                    "Security",
                }
                and candidate.args
            ):
                name = ast.unparse(candidate.args[0])[:500]
                target, _ = self.resolve(name, symbol)
                if self._auth_signal(name, target):
                    auth.append(name)
        # Include decorators as evidence, but don't claim they enforce authorization.
        auth.extend(
            ast.unparse(d)[:500]
            for d in node.decorator_list
            if any(
                key in ast.unparse(d).lower()
                for key in ("login_required", "auth_required", "roles_required")
            )
        )
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            verb = decorator.func.attr.lower()
            if verb not in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
                "head",
                "options",
                "route",
                "api_route",
            }:
                continue
            arg = decorator.args[0] if decorator.args else None
            if arg is None or not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                self.issues.append(
                    IndexIssue(
                        path=symbol.path,
                        reason=(
                            f"Route decorator @{verb} at line {decorator.lineno} on "
                            f"{symbol.qualified_name} uses a non-literal path"
                            + (f" ({ast.unparse(arg)[:200]})" if arg is not None else "")
                            + "; route not indexed"
                        ),
                    )
                )
                continue
            path = arg.value
            if len(path) > 500:
                self.issues.append(
                    IndexIssue(
                        path=symbol.path,
                        reason=(
                            f"Route decorator @{verb} at line {decorator.lineno} on "
                            f"{symbol.qualified_name} has an overlong path; route not indexed"
                        ),
                    )
                )
                continue
            methods = [verb.upper()] if verb not in {"route", "api_route"} else ["GET"]
            dependencies = list(auth)
            for kw in decorator.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    methods = [
                        v.value.upper()
                        for v in kw.value.elts
                        if isinstance(v, ast.Constant) and isinstance(v.value, str)
                    ]
                if kw.arg == "dependencies":
                    for dep in ast.walk(kw.value):
                        if isinstance(dep, ast.Call) and dep.args:
                            name = ast.unparse(dep.args[0])[:500]
                            target, _ = self.resolve(name, symbol)
                            if self._auth_signal(name, target):
                                dependencies.append(name)
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    name = ast.unparse(call.func)[:500]
                    target, _ = self.resolve(name, symbol)
                    if target and self._ownership_helper(target):
                        dependencies.append(name)
            for method in methods:
                self.entries.append(
                    EntryPoint(
                        id=stable_id("ent", symbol.id, method, path),
                        snapshot_id=self.ref.snapshot_id,
                        kind=EntryPointKind.http_route,
                        handler_symbol_id=symbol.id,
                        method=method,
                        path_template=path,
                        auth_dependencies=sorted(set(dependencies)),
                    )
                )

    def _expectations(
        self, file: SourceFile, symbol: Symbol, node: Definition | ast.Module
    ) -> None:
        import re

        if symbol.docstring:
            for line in symbol.docstring.splitlines():
                if re.search(r"\b(must|only|never|shall|required)\b", line, re.I):
                    statement = line.strip()[:500]
                    self._expectation(
                        symbol,
                        symbol,
                        statement,
                        0.65,
                        "ambiguous",
                        "Declared docstring rule; not a verified test result",
                    )
        if isinstance(node, ast.ClassDef):
            for member in node.body:
                if not isinstance(member, ast.AnnAssign) or not isinstance(member.value, ast.Call):
                    continue
                if ast.unparse(member.value.func).split(".")[-1] != "Field":
                    continue
                constraints = [
                    f"{kw.arg}={ast.unparse(kw.value)}"
                    for kw in member.value.keywords
                    if kw.arg
                    in {
                        "gt",
                        "ge",
                        "lt",
                        "le",
                        "min_length",
                        "max_length",
                        "pattern",
                        "max_digits",
                        "decimal_places",
                    }
                ]
                if constraints:
                    self._expectation(
                        symbol,
                        symbol,
                        f"{ast.unparse(member.target)} requires {', '.join(constraints)}",
                        0.7,
                        "ambiguous",
                        "Declared Field constraints, not executed",
                        start=member.lineno,
                        end=member.end_lineno,
                    )
        if not file.is_test or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return
        assertions = [n for n in ast.walk(node) if isinstance(n, ast.Assert)]
        if not assertions:
            return
        # Link test intent to actual statically resolved subjects, never name similarity alone.
        subjects = sorted(
            {
                str(e.callee_id)
                for e in self.calls
                if str(e.caller_id) == str(symbol.id) and e.callee_id is not None
            }
        )
        for subject_id in subjects or [symbol.id]:
            subject = next(s for s in self.symbols if str(s.id) == str(subject_id))
            assertion = assertions[0]
            self._expectation(
                subject,
                symbol,
                f"Test {node.name}: {ast.unparse(assertion.test)}"[:500],
                0.9,
                "ambiguous",
                "Extracted test intent; assertion has not been executed",
                start=assertion.lineno,
                end=assertion.end_lineno,
            )

    def _expectation(
        self,
        subject: Symbol,
        source: Symbol,
        statement: str,
        confidence: float,
        status: str,
        note: str,
        start: int | None = None,
        end: int | None = None,
    ) -> None:
        import re

        text = statement.lower()
        kind = (
            "ownership"
            if any(k in text for k in ("owner", "tenant", "other_customer"))
            or re.search(r"\bown\b", text)
            else (
                "authorization"
                if any(k in text for k in ("auth", "permission", "403"))
                else (
                    "validation"
                    if any(k in text for k in ("refund", "bound", "positive"))
                    else "functional"
                )
            )
        )
        self.expectations.append(
            BehavioralExpectation(
                id=stable_id(
                    "exp", self.ref.snapshot_id, subject.qualified_name, source.path, statement
                ),
                snapshot_id=self.ref.snapshot_id,
                subject_ref=subject.id,
                statement=statement,
                kind=ExpectationKind(kind),
                sources=[citation(self.ref, source, start=start, end=end, note=note)],
                confidence=confidence,
                origin=ExpectationOrigin.extracted,
                status=ExpectationStatus(status),
                data_sensitivity=DataSensitivity.financial
                if any(k in text for k in ("payment", "refund", "invoice"))
                else DataSensitivity.internal,
            )
        )

    def _link_http_tests(self) -> None:
        """Heuristic links between test HTTP literals and declared routes, never coverage proof."""
        import re

        symbols = {s.id: s for s in self.symbols}
        files = {f.path: f for f in self.files}
        for test in self.symbols:
            node = self.nodes[test.id]
            if not files[test.path].is_test or not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            assertions = [a for a in ast.walk(node) if isinstance(a, ast.Assert)]
            if not assertions:
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                    continue
                method = call.func.attr.upper()
                if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}:
                    continue
                if not call.args or not isinstance(call.args[0], ast.Constant):
                    continue
                request_path = call.args[0].value
                if not isinstance(request_path, str):
                    continue
                for entry in self.entries:
                    pattern = re.sub(
                        r"\\\{[^}]+\\\}", r"[^/]+", re.escape(entry.path_template or "")
                    )
                    if method != entry.method or not re.fullmatch(
                        pattern, request_path.split("?", 1)[0]
                    ):
                        continue
                    subject = symbols[entry.handler_symbol_id]
                    self.calls.append(
                        CallEdge(
                            snapshot_id=self.ref.snapshot_id,
                            caller_id=test.id,
                            callee_id=subject.id,
                            callee_name=f"HTTP {method} {request_path}"[:500],
                            resolution=CallResolution.likely,
                        )
                    )
                    self._expectation(
                        subject,
                        test,
                        f"HTTP test {node.name}: {ast.unparse(assertions[0].test)}"[:500],
                        0.75,
                        "ambiguous",
                        "Heuristic HTTP test link, not executed",
                        start=assertions[0].lineno,
                        end=assertions[0].end_lineno,
                    )

    def _document_expectations(self) -> None:
        """Declared intent from docs remains unverified and source-cited."""
        import re
        from itertools import islice

        # Hoisted out of the per-line loop: docs lines x symbols dominated indexing time.
        named = [
            (s, s.qualified_name.lower())
            for s in self.symbols
            if s.kind.value in {"module", "function", "method"}
        ]
        for file in self.files:
            if PurePosixPath(file.path).suffix not in {".md", ".rst"}:
                continue
            for number, line in enumerate(file.content.splitlines(), 1):
                if not re.search(r"\b(must|only|never|shall|required)\b", line, re.I):
                    continue
                words = set(re.findall(r"[a-z]{4,}", line.lower())) - {
                    "must",
                    "only",
                    "never",
                    "shall",
                    "required",
                    "their",
                    "that",
                    "this",
                }
                stems = {w.rstrip("s") for w in words}
                matches = (s for s, name in named if any(w in name for w in stems))
                for subject in islice(matches, 3):
                    evidence = EvidenceRef(
                        kind=EvidenceKind.source_span,
                        path=file.path,
                        commit_sha=self.ref.commit_sha,
                        span=SourceSpan(start_line=number, end_line=number),
                        provenance=f"code_intelligence@{self.ref.index_version}",
                        note="Declared documentation intent; lexical subject link is uncertain",
                    )
                    self.expectations.append(
                        BehavioralExpectation(
                            id=stable_id(
                                "exp", self.ref.snapshot_id, subject.id, file.path, str(number)
                            ),
                            snapshot_id=self.ref.snapshot_id,
                            subject_ref=subject.id,
                            statement=line.strip()[:500],
                            kind=ExpectationKind.functional,
                            sources=[evidence],
                            confidence=0.55,
                            origin=ExpectationOrigin.extracted,
                            status=ExpectationStatus.ambiguous,
                        )
                    )

    def _auth_signal(self, name: str, target: Symbol | None) -> bool:
        import re

        if target:
            node = self.nodes[target.id]
            if any(
                isinstance(n, ast.Raise)
                and n.exc is not None
                and any(
                    status in ast.unparse(n.exc)
                    for status in ("HTTP_401", "HTTP_403", "401", "403")
                )
                for n in self._local_nodes(node)
            ):
                return True
            if self._ownership_helper(target):
                return True
        # Naming convention, not evidence that the callable enforces authentication.
        return bool(
            re.search(
                r"(?:^|\.)(?:current_user|current_customer|authenticate|"
                r"require_auth|require_user|login_required)$",
                name,
            )
        )

    def _ownership_helper(self, symbol: Symbol) -> bool:
        if symbol.kind not in {SymbolKind.function, SymbolKind.method}:
            return False
        node = self.nodes[symbol.id]
        comparisons = [n for n in self._local_nodes(node) if isinstance(n, ast.Compare)]

        def identity(value: ast.AST) -> bool:
            text = ast.unparse(value).lower()
            return any(word in text for word in ("owner", "tenant", "customer"))

        identity_comparison = any(
            any(isinstance(op, (ast.Eq, ast.NotEq)) for op in n.ops)
            and identity(n.left)
            and any(identity(other) for other in n.comparators)
            and (".id" in ast.unparse(n) or "_id" in ast.unparse(n))
            for n in comparisons
        )
        rejects = any(
            isinstance(n, ast.Raise)
            and n.exc is not None
            and any(
                status in ast.unparse(n.exc)
                for status in (
                    "HTTP_401",
                    "HTTP_403",
                    "HTTP_404",
                    "401",
                    "403",
                    "404",
                )
            )
            for n in self._local_nodes(node)
        )
        return identity_comparison and rejects

    @staticmethod
    def _local_nodes(node: ast.AST) -> Iterator[ast.AST]:
        pending = [node]
        while pending:
            current = pending.pop()
            yield current
            pending.extend(
                child
                for child in ast.iter_child_nodes(current)
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            )

    def _route_ownership_expectations(self) -> None:
        """Attach declared module intent and flag conservative sibling-check inconsistencies.

        These are ambiguous expectations, never vulnerability verdicts or runtime proof.
        Only resource-by-id GET siblings in the same lexical app/router participate.
        """
        import re

        by_id = {s.id: s for s in self.symbols}
        helpers = {s.id for s in self.symbols if self._ownership_helper(s)}
        checks: dict[str, list[tuple[Symbol, ast.Call]]] = defaultdict(list)
        groups: dict[tuple[str, str], list[EntryPoint]] = defaultdict(list)
        for entry in self.entries:
            handler = by_id[entry.handler_symbol_id]
            node = self.nodes[handler.id]
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    target, _ = self.resolve(ast.unparse(call.func)[:500], handler)
                    if target and target.id in helpers:
                        checks[entry.id].append((target, call))
            if entry.method == "GET" and "{" in (entry.path_template or ""):
                receiver = ""
                for decorator in getattr(node, "decorator_list", []):
                    if isinstance(decorator, ast.Call) and isinstance(
                        decorator.func, ast.Attribute
                    ):
                        receiver = ast.unparse(decorator.func.value)
                        break
                groups[(handler.qualified_name.rsplit(".", 1)[0], receiver)].append(entry)

            module = self.owners[id(self.trees[handler.path])]
            module_node = self.trees[handler.path]
            if module.docstring and module_node.body and entry.method == "GET":
                doc_node = module_node.body[0]
                for statement in module.docstring.splitlines():
                    text = statement.lower()
                    resource_words = set(re.findall(r"[a-z]+", entry.path_template or ""))
                    matches_resource = any(
                        word.rstrip("s") in text for word in resource_words if len(word) >= 4
                    )
                    if (
                        matches_resource
                        and re.search(r"\bown\b|owner|tenant", text)
                        and any(word in text for word in ("only", "must", "never"))
                    ):
                        self.expectations.append(
                            BehavioralExpectation(
                                id=stable_id(
                                    "exp", self.ref.snapshot_id, entry.id, "module", statement
                                ),
                                snapshot_id=self.ref.snapshot_id,
                                subject_ref=entry.id,
                                statement=statement.strip()[:500],
                                kind=ExpectationKind.ownership,
                                sources=[
                                    citation(
                                        self.ref,
                                        module,
                                        start=doc_node.lineno,
                                        end=doc_node.end_lineno,
                                        note="Module rule linked by route resource; unverified",
                                    )
                                ],
                                confidence=0.55,
                                origin=ExpectationOrigin.extracted,
                                status=ExpectationStatus.ambiguous,
                                data_sensitivity=DataSensitivity.financial
                                if any(
                                    word in (entry.path_template or "")
                                    for word in ("payment", "refund")
                                )
                                else DataSensitivity.internal,
                            )
                        )
        for entries in groups.values():
            covered = [e for e in entries if checks[e.id]]
            if len(covered) < 2 or len(covered) * 2 <= len(entries):
                continue
            common = set.intersection(*[{s.id for s, _ in checks[e.id]} for e in covered])
            if not common:
                continue
            for outlier in [e for e in entries if not checks[e.id]]:
                sources = []
                for sibling in covered:
                    handler = by_id[sibling.handler_symbol_id]
                    for helper, call in checks[sibling.id]:
                        if helper.id in common:
                            sources.append(
                                citation(
                                    self.ref,
                                    handler,
                                    start=call.lineno,
                                    end=call.end_lineno,
                                    note=(
                                        f"Sibling {sibling.method} "
                                        f"{sibling.path_template} ownership call"
                                    ),
                                )
                            )
                helper_names = ", ".join(sorted(by_id[i].qualified_name for i in common))
                self.expectations.append(
                    BehavioralExpectation(
                        id=stable_id("exp", self.ref.snapshot_id, outlier.id, "sibling-ownership"),
                        snapshot_id=self.ref.snapshot_id,
                        subject_ref=outlier.id,
                        statement=(
                            "A customer should only read their own resource here; sibling "
                            f"routes enforce {helper_names}. Possible ownership inconsistency."
                        )[:500],
                        kind=ExpectationKind.ownership,
                        sources=sources,
                        confidence=0.6,
                        origin=ExpectationOrigin.extracted,
                        status=ExpectationStatus.ambiguous,
                        data_sensitivity=DataSensitivity.financial
                        if any(
                            word in (outlier.path_template or "") for word in ("payment", "refund")
                        )
                        else DataSensitivity.internal,
                    )
                )


def dependencies(files: list[SourceFile]) -> list[DependencyRecord]:
    output: list[DependencyRecord] = []
    for file in files:
        name = PurePosixPath(file.path).name
        requirements: list[str] = []
        if name == "pyproject.toml":
            try:
                data = tomllib.loads(file.content)
                requirements = data.get("project", {}).get("dependencies", [])
            except (tomllib.TOMLDecodeError, AttributeError):
                continue
        elif name.startswith("requirements") and name.endswith(".txt"):
            requirements = [line.split("#", 1)[0].strip() for line in file.content.splitlines()]
        import re

        for requirement in requirements:
            if not isinstance(requirement, str):
                continue
            match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(.*)$", requirement)
            if match:
                output.append(DependencyRecord(path=file.path, name=match[1], constraint=match[2]))
    return output
