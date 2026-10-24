"""Generic scripted FakeLLM policy for the investigation eval.

This is NOT a model and NOT an approximation of Claude. It is a deterministic,
pattern-driven baseline ("a simple SAST script wearing the agent harness")
whose only purpose is to drive the real orchestrator, real tools, real check
templates and real sandbox end-to-end so the harness can be validated without
spending provider budget. Every detection below comes from tool results the
script actually requested (search hits, static.analyze output, read spans) —
it never reads cases.json and never sees ground truth.

Detection strategy (the same moves a competent reviewer makes):
  * read build_recipe.json for the approved routes/identities;
  * read every application source file fully (they are small fixtures);
  * flag guards that are defined but never called (dead guards);
  * flag resource GET handlers that skip the guard their siblings call;
  * flag missing accumulated-field bounds, unsafe pattern signals
    (shell=True, f-string SQL, unverified TLS), dead allow-list constants,
    non-constant-time secret compares, insecure cookie flags, missing
    tar filters, and dead confinement helpers;
  * cite the exact span that was read.
"""

from __future__ import annotations

import json
import re
from typing import Any

from security_agents.contracts import AgentName
from security_agents.llm.providers import FakeStep, FakeTurn, last_tool_results
from security_agents.llm.types import LLMRequest

# --------------------------------------------------------------------------- helpers

_BLOCK = re.compile(r"<data label='([^']+)' provenance='[^']*'>\n(.*?)\n</data>", re.S)


def _blocks(request: LLMRequest) -> dict[str, Any]:
    """Parse every <data label> block in the first user message."""
    out: dict[str, Any] = {}
    if not request.messages:
        return out
    content = request.messages[0]["content"]
    text = content if isinstance(content, str) else json.dumps(content, default=str)
    for label, body in _BLOCK.findall(text):
        try:
            out[label] = json.loads(body)
        except ValueError:
            out[label] = body
    return out


def _tool_history(request: LLMRequest) -> list[tuple[str, dict[str, Any]]]:
    """All (tool_name, parsed_result_json) pairs, oldest first.

    Assistant tool_use blocks and the user tool_result blocks that answer them
    both appear in order, so pairing by position is exact.
    """
    names: list[str] = []
    for m in request.messages:
        if m["role"] != "assistant":
            continue
        content = m["content"]
        if isinstance(content, list):
            names.extend(b.get("name", "") for b in content if b.get("type") == "tool_use")
    results: list[dict[str, Any]] = []
    for m in request.messages:
        if m["role"] != "user" or not isinstance(m["content"], list):
            continue
        for block in m["content"]:
            if block.get("type") != "tool_result":
                continue
            text = block.get("content")
            if isinstance(text, list):
                text = "".join(b.get("text", "") for b in text)
            parsed: dict[str, Any] = {"raw": text, "is_error": bool(block.get("is_error"))}
            try:
                parsed["json"] = json.loads(text[text.index("{") : text.rindex("}") + 1])
            except (ValueError, AttributeError, TypeError):
                parsed["json"] = None
            results.append(parsed)
    return list(zip(names, results, strict=False))


_LINENO = re.compile(r"^ *\d+  ")


def _read_files(request: LLMRequest) -> dict[str, dict[str, Any]]:
    """path -> {content, start_line} for every code.read_span result so far.

    read_span prefixes each line as f"{n:>5}  {line}"; strip that prefix so
    downstream regexes see real source text (line numbers are unchanged).
    """
    out: dict[str, dict[str, Any]] = {}
    for name, res in _tool_history(request):
        if name != "code__read_span":
            continue
        j = res.get("json") or {}
        if j.get("path") and j.get("content") is not None:
            j["content"] = "\n".join(_LINENO.sub("", ln, count=1) for ln in j["content"].splitlines())
            out[j["path"]] = j
    return out


def _search_hits(request: LLMRequest) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for name, res in _tool_history(request):
        if name == "code__search" and isinstance(res.get("json"), dict):
            hits.extend(res["json"].get("hits") or [])
    return hits


def _analyses(request: LLMRequest) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, res in _tool_history(request):
        if name == "static__analyze" and isinstance(res.get("json"), dict):
            j = res["json"]
            if j.get("path"):
                out[j["path"]] = j
    return out


def _recipe(request: LLMRequest) -> dict[str, Any]:
    for path, span in _read_files(request).items():
        if path == "build_recipe.json":
            try:
                return json.loads(span["content"])
            except ValueError:
                return {}
    return {}


_GUARD_DEF = re.compile(
    r"^\s*def\s+((?:require|verify|check|safe|clamp|validate)_[a-z_]*|session_valid|delivery_is_fresh)\s*\("
)
_DEF = re.compile(r"^(\s*)def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
_ROUTE_DEC = re.compile(r'@(app|router)\.(get|post|put|delete|patch)\(\s*"([^"]+)"')
_DEAD_CONST = re.compile(r"^\s*(ALLOWED_[A-Z_]+|ALLOW_[A-Z_]+)\s*[:=]")


def _function_bodies(text: str) -> dict[str, tuple[int, int, str]]:
    """symbol -> (start_line, end_line, body) via indentation slicing."""
    lines = text.splitlines()
    out: dict[str, tuple[int, int, str]] = {}
    for i, line in enumerate(lines):
        m = _DEF.match(line)
        if not m:
            continue
        indent, name = len(m.group(1)), m.group(2)
        start = i + 1
        end = start
        for j in range(i + 1, len(lines)):
            other = _DEF.match(lines[j])
            if other and len(other.group(1)) <= indent:
                break
            if lines[j].strip() and not lines[j].startswith((" ", "\t")):
                break
            end = j + 1
        out[name] = (start, end, "\n".join(lines[i:end]))
    return out


def _handler_for_route(
    bodies: dict[str, tuple[int, int, str]], text: str, method: str, path_template: str
) -> tuple[str, int, int, str] | None:
    """Find the handler function for a route via its decorator line."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _ROUTE_DEC.search(line)
        if not m:
            continue
        if m.group(2).upper() != method.upper():
            continue
        if m.group(3) != path_template:
            continue
        for j in range(i + 1, min(i + 8, len(lines))):
            dm = _DEF.match(lines[j])
            if dm:
                name = dm.group(2)
                if name in bodies:
                    s, e, body = bodies[name]
                    return name, s, e, body
                return None
        return None
    return None


def _calls_of(name: str, body: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\s*\(", body) is not None


# CWE family map for scoring-side reference (kept here for one mapping source).
CWE_CATEGORY = {
    "CWE-639": "access_control.ownership",
    "CWE-862": "access_control.ownership",
    "CWE-863": "access_control.ownership",
    "CWE-613": "access_control.authentication",
    "CWE-347": "access_control.authentication",
    "CWE-614": "access_control.authentication",
    "CWE-208": "access_control.authentication",
    "CWE-306": "access_control.authentication",
    "CWE-1284": "input_validation.bounds",
    "CWE-770": "input_validation.bounds",
    "CWE-89": "input_validation.bounds",
    "CWE-78": "input_validation.bounds",
    "CWE-22": "input_validation.bounds",
    "CWE-918": "input_validation.bounds",
    "CWE-95": "input_validation.bounds",
    "CWE-295": "access_control.authentication",
    "CWE-489": "input_validation.bounds",
    "CWE-942": "input_validation.bounds",
}


# --------------------------------------------------------------------------- scout


def _scout_step0(request: LLMRequest) -> FakeTurn:
    # Two searches: code.search caps the pattern at 200 chars.
    return FakeTurn(
        tool_calls=[
            ("expectations__for_subject", {}),
            (
                "code__search",
                {
                    "pattern": r"require_owner|require_tenant|verify_signature|compare_digest|safe_upload_path|session_valid|delivery_is_fresh|clamp_limit",
                    "max_results": 50,
                },
            ),
            (
                "code__search",
                {
                    "pattern": r"ALLOWED_|extractall|_create_unverified_context|create_default_context|set_cookie|share_token|urlopen|execute\(|subprocess",
                    "max_results": 50,
                },
            ),
            ("code__search", {"pattern": r"^(import|from)\s", "max_results": 50}),
            ("code__read_span", {"path": "build_recipe.json", "start_line": 1, "end_line": 300}),
        ]
    )


def _scout_step1(request: LLMRequest) -> FakeTurn:
    """Read every application source file fully + run static.analyze on it."""
    candidates: set[str] = set()
    for hit in _search_hits(request):
        p = hit.get("path", "")
        if p.endswith(".py"):
            candidates.add(p)
    for ep in _blocks(request).get("entry_points") or []:
        p = str(ep.get("file", ""))
        if p.endswith(".py"):
            candidates.add(p)
    recipe = _recipe(request)
    if recipe:
        candidates.add("build_recipe.json")
    calls: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(candidates):
        if not path.endswith(".py"):
            continue
        calls.append(("static__analyze", {"path": path}))
        calls.append(("code__read_span", {"path": path, "start_line": 1, "end_line": 400}))
    # Stay under the 40-call budget even on larger trees.
    return FakeTurn(tool_calls=calls[:24])


def _scout_step2(request: LLMRequest) -> FakeTurn:
    """Detect defects from what was actually read; submit hypotheses."""
    recipe = _recipe(request)
    analyses = _analyses(request)
    files = _read_files(request)
    expectations = []
    for name, res in _tool_history(request):
        if name == "expectations__for_subject" and isinstance(res.get("json"), dict):
            expectations = res["json"].get("expectations") or []
    entry_points = _blocks(request).get("entry_points") or []
    entry_by_route = {
        (str(e.get("method", "")).upper(), e.get("path_template")): e
        for e in entry_points
        if isinstance(e, dict)
    }

    hypotheses: list[dict[str, Any]] = []
    guards_defined: set[str] = set()

    def cite(path: str, start: int, end: int) -> dict[str, list]:
        return {"paths": [path], "starts": [start], "ends": [end]}

    def exp_match(*words: str) -> list[str]:
        out = []
        for e in expectations:
            stmt = (e.get("statement", "") + " " + str(e.get("subject_ref", ""))).lower()
            if any(w in stmt for w in words):
                out.append(e["id"])
        return out[:3]

    def add(title, cwe, path, start, end, invariant, exps, confidence, entry=None):
        hypotheses.append(
            {
                "title": title[:200],
                "weakness_class": cwe,
                "rationale": f"Detected by scripted pattern scan: {title}. Cited span read via code.read_span.",
                "expected_invariant": invariant[:200],
                "cited_paths": [path],
                "cited_start_lines": [start],
                "cited_end_lines": [end],
                "expectation_ids": exps,
                "entry_point_id": entry,
                "confidence": confidence,
                "suggested_check_category": CWE_CATEGORY.get(cwe, "input_validation.bounds"),
            }
        )

    for path, ana in analyses.items():
        span = files.get(path)
        if span is None:
            continue
        text = span["content"]
        base_line = int(span.get("start_line", 1))
        bodies = _function_bodies(text)
        lines = text.splitlines()

        def abs_lines(s: int, e: int, base: int = base_line) -> tuple[int, int]:
            return base + s - 1, base + e - 1

        # -- signals from static.analyze (string-built SQL, subprocess, eval, ...)
        for sig in ana.get("pattern_signals") or []:
            cwe = sig.get("cwe", "")
            if cwe not in CWE_CATEGORY:
                continue
            ln = int(sig.get("line", 1))
            a, b = abs_lines(ln, ln)
            add(
                f"{sig.get('signal', 'risky pattern')} at {path}:{a}",
                cwe,
                path,
                a,
                b,
                "dangerous primitive must not appear in shipped code",
                exp_match("injection", "sql", "command", "input"),
                "high",
            )

        # -- dead guards: defined helper never called anywhere else in the file
        for fn in bodies:
            if not _GUARD_DEF.match(next((ln for ln in lines if f"def {fn}(" in ln), "")):
                continue
            all_text = "\n".join(s.get("content", "") for s in files.values())
            # Count call sites `fn(`: imports/docs mention the name without
            # calling it; the `def fn(` line itself is subtracted.
            call_refs = len(re.findall(rf"\b{re.escape(fn)}\s*\(", all_text))
            callers = call_refs - 1
            if callers > 0:
                continue
            s, e, _ = bodies[fn]
            a, b = abs_lines(s, min(e, s + 12))
            # The defect is the *missing call*, not the dead definition: when
            # another module still imports the guard, cite that file (the
            # place the check belongs) rather than the helper's def line.
            cite_path, ca, cb = path, a, b
            for p2, span2 in files.items():
                if p2 == path or not p2.endswith(".py"):
                    continue
                for i2, l2 in enumerate(span2["content"].splitlines(), start=1):
                    if re.search(rf"^(from\s+\S+\s+import\b|import\s+\S+).*\b{re.escape(fn)}\b", l2):
                        cite_path = p2
                        base2 = int(span2.get("start_line", 1))
                        ca, cb = base2 + i2 - 1, base2 + i2 - 1
                        break
                if cite_path != path:
                    break
            if fn.startswith(("require_", "check_")):
                cwe, inv, words = (
                    "CWE-862",
                    "the guard this helper enforces must run",
                    ("authoriz", "own", "tenant"),
                )
            elif fn.startswith("safe_"):
                cwe, inv, words = (
                    "CWE-22",
                    "paths must stay confined to their root",
                    ("path", "confine", "upload"),
                )
            elif fn.startswith("verify_"):
                cwe, inv, words = (
                    "CWE-347",
                    "the signed request must be verified",
                    ("signature", "webhook", "hmac"),
                )
            elif fn in ("session_valid",):
                cwe, inv, words = (
                    "CWE-613",
                    "expired sessions must be rejected",
                    ("session", "expire", "token"),
                )
            elif fn in ("delivery_is_fresh",):
                cwe, inv, words = "CWE-347", "stale deliveries must be rejected", ("replay", "stale", "fresh")
            elif fn.startswith("clamp_"):
                cwe, inv, words = "CWE-770", "list sizes must stay bounded", ("clamp", "page", "limit")
            else:
                cwe, inv, words = "CWE-862", "the guard this helper enforces must run", ("authoriz",)
            add(
                f"guard {fn} is imported but never called",
                cwe,
                cite_path,
                ca,
                cb,
                inv,
                exp_match(*words),
                "medium",
            )

        # -- weakened confinement helpers: a `safe_*` path builder that no
        # longer normalizes or bounds its result.
        for fn, (s, e, body) in bodies.items():
            if not fn.startswith("safe_"):
                continue
            has_normalize = "resolve(" in body or "abspath" in body or "normpath" in body
            has_contain = any(
                w in body for w in ("relative_to", "is_relative_to", "commonpath", "startswith", "raise")
            )
            if has_normalize and has_contain:
                continue
            a, b = abs_lines(s, min(e, s + 12))
            add(
                f"confinement helper {fn} no longer normalizes or bounds the returned path",
                "CWE-22",
                path,
                a,
                b,
                "paths must stay confined to their root",
                exp_match("path", "confine", "upload", "traversal"),
                "high",
            )

        # -- dead allow-list constants
        for i, line in enumerate(lines, start=1):
            m = _DEAD_CONST.match(line)
            if not m:
                continue
            name = m.group(1)
            uses = sum(1 for l2 in lines if name in l2) - 1
            if uses == 0:
                a, b = abs_lines(i, i)
                add(
                    f"allow-list {name} is defined but never enforced",
                    "CWE-918" if "HOST" in name else "CWE-862",
                    path,
                    a,
                    b,
                    "the allow-list must gate its requests",
                    exp_match("allow", "host", "fetch", "outbound"),
                    "medium",
                )

        # -- non-constant-time secret compares
        for fname, (s, _e, body) in bodies.items():
            if "compare_digest" in body:
                continue
            for ln_i, bl in enumerate(body.splitlines(), start=s):
                if re.search(r"(share_token|token|secret|signature)\s*(!=|==)\s*", bl) or re.search(
                    r"(!=|==)\s*(share_token|token|secret|signature)\b", bl
                ):
                    a, b2 = abs_lines(ln_i, ln_i)
                    add(
                        f"{fname} compares a secret with plain equality",
                        "CWE-208",
                        path,
                        a,
                        b2,
                        "secrets must be compared in constant time",
                        exp_match("constant", "compare", "share", "token"),
                        "high",
                    )
                    break

        # -- cookie flags
        for m in re.finditer(r"set_cookie\(", text):
            call_end = text.find(")", m.start())
            call_text = text[m.start() : call_end if call_end > 0 else m.start() + 300]
            if "secure=True" not in call_text:
                ln = text[: m.start()].count("\n") + 1
                a, b = abs_lines(ln, ln)
                add(
                    "session cookie is set without secure=True",
                    "CWE-614",
                    path,
                    a,
                    b,
                    "session cookies must be Secure/HttpOnly/SameSite",
                    exp_match("cookie", "secure", "session"),
                    "high",
                )

        # -- tar extraction without a member filter
        for m in re.finditer(r"extractall\(([^)]*)\)", text):
            if "filter=" not in m.group(1):
                ln = text[: m.start()].count("\n") + 1
                a, b = abs_lines(ln, ln)
                add(
                    "tarfile.extractall without member filter",
                    "CWE-22",
                    path,
                    a,
                    b,
                    "archive members must not escape the staging dir",
                    exp_match("extract", "staging", "confine"),
                    "high",
                )

        # -- unverified TLS context
        for m in re.finditer(r"_create_unverified_context", text):
            ln = text[: m.start()].count("\n") + 1
            a, b = abs_lines(ln, ln)
            add(
                "TLS certificate verification disabled",
                "CWE-295",
                path,
                a,
                b,
                "outbound TLS must verify certificates",
                exp_match("tls", "verif", "certificate"),
                "high",
            )

        # -- SSRF: urlopen in a handler while an allow-list constant is dead
        #    (handled above) OR urlopen with no allow-list defined at all
        if "urlopen(" in text and not _DEAD_CONST.search(text) and "ALLOWED_" not in text:
            for fname, (s, e, body) in bodies.items():
                if "urlopen(" in body:
                    a, b = abs_lines(s, e)
                    add(
                        f"{fname} fetches a URL with no host allow-list",
                        "CWE-918",
                        path,
                        a,
                        b,
                        "outbound fetches must be host allow-listed",
                        exp_match("fetch", "allow", "host"),
                        "medium",
                    )
                    break

        # -- resource GET routes that skip the guard their siblings call.
        # Guards may live in a different module than the handlers (e.g. a
        # shared util in a monorepo), so collect them across every file read.
        for p3, span3 in files.items():
            if not p3.endswith(".py"):
                continue
            for ln in span3["content"].splitlines():
                gm = _GUARD_DEF.match(ln)
                if gm:
                    guards_defined.add(gm.group(1))
        get_handlers: list[tuple[str, str]] = []  # (body_text, name)
        routes = recipe.get("routes", []) if recipe else []
        # Every handler that calls a guard counts as a guarded sibling — the
        # sibling may be a list or POST route, not only another `{id}` GET.
        for route in routes:
            for p2 in analyses:
                span2 = files.get(p2)
                if span2 is None:
                    continue
                hit = _handler_for_route(
                    _function_bodies(span2["content"]),
                    span2["content"],
                    str(route.get("method")),
                    str(route.get("path_template")),
                )
                if hit:
                    get_handlers.append((hit[3], hit[0]))
        if get_handlers:
            for route in routes:
                if str(route.get("method", "")).upper() != "GET" or "{" not in str(
                    route.get("path_template", "")
                ):
                    continue
                if not route.get("resource"):
                    continue
                hit = _handler_for_route(bodies, text, str(route["method"]), str(route["path_template"]))
                if not hit:
                    continue
                name, s, e, body = hit
                calls_guard = any(_calls_of(g, body) for g in guards_defined)
                siblings_guarded = any(
                    any(_calls_of(g, b2) for g in guards_defined) for b2, n2 in get_handlers if n2 != name
                )
                if not calls_guard and siblings_guarded:
                    a, b = abs_lines(s, e)
                    entry = entry_by_route.get((str(route["method"]).upper(), route["path_template"]))
                    add(
                        f"{name} returns a resource without the ownership guard its siblings call",
                        "CWE-639",
                        path,
                        a,
                        b,
                        "a caller may only read resources they own",
                        exp_match("own", "authoriz", "tenant"),
                        "medium",
                        entry=entry.get("id") if entry else None,
                    )

        # -- accumulated-field bounds on POST routes with a bound_source
        for route in routes:
            if str(route.get("method", "")).upper() != "POST":
                continue
            bound = route.get("bound_source") or {}
            accumulated = route.get("accumulated_field")
            if not accumulated or not bound:
                continue
            hit = _handler_for_route(bodies, text, "POST", str(route.get("path_template", "")))
            if not hit:
                continue
            name, s, e, body = hit
            # The bound check must compare the *accumulated* total plus the new
            # value against the bound, on the same expression line.
            bound_lines = [ln for ln in body.splitlines() if ">" in ln or ">=" in ln]
            bound_uses_accumulated = any(str(accumulated) in ln for ln in bound_lines)
            if bound_lines and not bound_uses_accumulated:
                a, b = abs_lines(s, e)
                entry = entry_by_route.get(("POST", route["path_template"]))
                add(
                    f"{name} validates the bound without the accumulated {accumulated} total",
                    "CWE-1284",
                    path,
                    a,
                    b,
                    "accumulated usage must never exceed the documented bound",
                    exp_match("bound", "amount", "exceed", "invoice", "payment"),
                    "high",
                    entry=entry.get("id") if entry else None,
                )

        # -- unused freshness/signature helpers already covered by dead guards

    # Collapse repeats of the same defect (e.g. two `subprocess.` signal
    # lines in one file) — keep one hypothesis per (cwe, path, title-stem).
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for h in hypotheses:
        key = (h["weakness_class"], h["cited_paths"][0], h["title"].rsplit(":", 1)[0])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
    hypotheses = deduped[:10]
    submit_args = {
        "hypotheses": hypotheses,
        "coverage": {
            "entry_points_reviewed": [str(e.get("id")) for e in entry_points][:40],
            "looks_correct": [],
            "skipped_with_reason": [],
        },
        "suspicious_instructions": [],
    }
    return FakeTurn(tool_calls=[("submit_scout_output", submit_args)])


# --------------------------------------------------------------------------- probe

_IDENTITY_RE = re.compile(r"\{[^}/]*\}")


def _same_route(a: str, b: str) -> bool:
    ma, _, pa = a.partition(" ")
    mb, _, pb = b.partition(" ")
    if ma.upper() != mb.upper():
        return False
    sa = _IDENTITY_RE.sub("{}", pa).strip("/").split("/")
    sb = _IDENTITY_RE.sub("{}", pb).strip("/").split("/")
    short, long_ = sorted((sa, sb), key=len)
    return len(short) > 1 and long_[len(long_) - len(short) :] == short


def _probe_submit(outcome, template_ref=None, run_id=None, observations=None, interpretation="", missing=""):
    return FakeTurn(
        tool_calls=[
            (
                "submit_probe_output",
                {
                    "outcome": outcome,
                    "check_template_ref": template_ref,
                    "sandbox_run_id": run_id,
                    "observations": observations or [],
                    "interpretation": interpretation[:500],
                    "missing_capability": missing[:200],
                },
            )
        ]
    )


def _probe_step0(request: LLMRequest) -> FakeTurn:
    return FakeTurn(
        tool_calls=[
            ("checks__list_templates", {}),
            ("code__read_span", {"path": "build_recipe.json", "start_line": 1, "end_line": 300}),
        ]
    )


def _probe_step1(request: LLMRequest) -> FakeTurn:
    hyp = _blocks(request).get("hypothesis") or {}
    recipe = _recipe(request)
    routes = recipe.get("routes", [])
    identities = sorted(recipe.get("identities", {}))
    category = hyp.get("suggested_check_category", "")
    subject = hyp.get("subject_routes") or []

    def recipe_route() -> dict[str, Any] | None:
        for s in subject:
            for r in routes:
                key = f"{str(r.get('method', 'GET')).upper()} {r.get('path_template')}"
                if _same_route(key, s):
                    return r
        return None

    if category in ("access_control.ownership", "authorization.ownership"):
        r = recipe_route()
        if r and len(identities) >= 2:
            return FakeTurn(
                tool_calls=[
                    (
                        "checks__run_template",
                        {
                            "template_ref": "access_control.ownership@1",
                            "target_id": hyp.get("target_id", "tgt_demo_target"),
                            "parameters": {
                                "route_id": r["id"],
                                "owner_identity": identities[0],
                                "other_identity": identities[1],
                            },
                        },
                    )
                ]
            )
        return _probe_submit(
            "inconclusive",
            missing=f"subject route {subject or 'unknown'} is not a resource route in the recipe or lacks two identities",
        )

    if category in ("input_validation.bounds", "input.bounds"):
        post = next(
            (
                r
                for r in routes
                if str(r.get("method", "")).upper() == "POST"
                and isinstance(r.get("body_template"), dict)
                and r.get("bound_source")
            ),
            None,
        )
        if post and identities:
            bound = post.get("bound_source") or {}
            params: dict[str, Any] = {
                "route_id": post["id"],
                "read_route_id": bound.get("route", post["id"]),
                "owner_identity": identities[0],
                "over_by": "10.00",
            }
            return FakeTurn(
                tool_calls=[
                    (
                        "checks__run_template",
                        {
                            "template_ref": "input_validation.bounds@1",
                            "target_id": hyp.get("target_id", "tgt_demo_target"),
                            "parameters": params,
                        },
                    )
                ]
            )
        return _probe_submit(
            "inconclusive",
            missing="no POST create route with body_template and bound_source exists in the recipe",
        )

    if category == "access_control.authentication":
        r = recipe_route() or next(
            (
                r
                for r in routes
                if str(r.get("method", "")).upper() == "GET"
                and len(_IDENTITY_RE.findall(str(r.get("path_template", "")))) <= 1
            ),
            None,
        )
        if r and identities:
            return FakeTurn(
                tool_calls=[
                    (
                        "checks__run_template",
                        {
                            "template_ref": "access_control.authentication@1",
                            "target_id": hyp.get("target_id", "tgt_demo_target"),
                            "parameters": {"route_id": r["id"], "identity": identities[0]},
                        },
                    )
                ]
            )
        return _probe_submit(
            "inconclusive",
            missing="no GET read route in the recipe for the authentication template",
        )

    return _probe_submit(
        "inconclusive",
        missing=f"no reviewed check template covers category {category!r} for this route set",
    )


def _probe_step2(request: LLMRequest) -> FakeTurn:
    results = last_tool_results(request)
    run = None
    for res in results:
        j = res.get("json")
        if isinstance(j, dict) and ("matches_expectation" in j or "sandbox_run_id" in j or "error" in j):
            run = j
    if run is None or run.get("error"):
        detail = (run or {}).get("error") or (run or {}).get("detail") or "check did not execute"
        return _probe_submit(
            "inconclusive",
            interpretation=f"check failed: {str(detail)[:300]}",
            missing="check execution error",
        )
    matches = run.get("matches_expectation")
    outcome = "confirmed" if matches is False else "refuted" if matches is True else "inconclusive"
    obs = []
    if isinstance(run.get("result"), dict):
        obs.extend(f"{k}={v}" for k, v in list(run["result"].items())[:6])
    if run.get("route"):
        obs.append(f"route={run['route']}")
    return _probe_submit(
        outcome,
        template_ref=run.get("template_ref"),
        run_id=run.get("sandbox_run_id"),
        observations=[str(o)[:200] for o in obs][:8],
        interpretation=f"executed template reported matches_expectation={matches}",
    )


# --------------------------------------------------------------------------- trace

_CVSS_8 = [
    ("AV", "N", "route is reachable over HTTP"),
    ("AC", "L", "no special conditions needed"),
    ("PR", "L", "a valid identity is required"),
    ("UI", "N", "no user interaction"),
    ("S", "U", "same security scope"),
    ("C", "L", "other customers' data may be read"),
    ("I", "L", "limited integrity impact"),
    ("A", "N", "no availability impact shown"),
]


def _trace_step0(request: LLMRequest) -> FakeTurn:
    finding = _blocks(request).get("finding") or {}
    paths = finding.get("affected_paths") or []
    if paths:
        return FakeTurn(
            tool_calls=[("code__read_span", {"path": paths[0], "start_line": 1, "end_line": 400})]
        )
    return FakeTurn(text="no affected path to read")


def _trace_step1(request: LLMRequest) -> FakeTurn:
    finding = _blocks(request).get("finding") or {}
    paths = finding.get("affected_paths") or ["unknown.py"]
    symbol = finding.get("affected_symbol")
    run_id = finding.get("sandbox_run_id")
    read = list(_read_files(request).values())
    if read:
        span = read[0]
        s, e = int(span.get("start_line", 1)), int(span.get("end_line", 1))
        evidence_path = span.get("path", paths[0])
    else:
        s, e, evidence_path = 1, 40, paths[0]
    submit = {
        "steps": [
            {
                "description": f"attacker reaches {evidence_path} over HTTP and the missing check lets the request through",
                "symbol_ref": symbol,
                "verified": bool(run_id),
                "evidence_path": evidence_path,
                "evidence_start_line": s,
                "evidence_end_line": min(e, s + 60),
                "evidence_artifact_id": run_id,
            }
        ],
        "preconditions": [
            "network access to the service",
            "a valid fixture identity where the route requires one",
        ],
        "impact_summary": f"{finding.get('weakness_class', 'weakness')} confirmed at {evidence_path}",
        "cvss_base_metrics": [
            {
                "metric": m,
                "value": v,
                "rationale": r,
                "evidence_path": evidence_path,
                "evidence_start_line": s,
                "evidence_end_line": min(e, s + 60),
                "evidence_artifact_id": run_id,
            }
            for m, v, r in _CVSS_8
        ],
        "severity_rationale": f"read-only {finding.get('weakness_class', '')} on authenticated data; scoped to this service",
        "followup_validation_suggestions": [],
    }
    return FakeTurn(tool_calls=[("submit_trace_output", submit)])


# --------------------------------------------------------------------------- diagnose


def _diagnose_step0(request: LLMRequest) -> FakeTurn:
    finding = _blocks(request).get("finding") or {}
    paths = finding.get("affected_paths") or []
    if paths:
        return FakeTurn(
            tool_calls=[("code__read_span", {"path": paths[0], "start_line": 1, "end_line": 400})]
        )
    return FakeTurn(text="no affected path to read")


def _diagnose_step1(request: LLMRequest) -> FakeTurn:
    return FakeTurn(
        tool_calls=[
            (
                "code__search",
                {
                    "pattern": r"require_owner|require_tenant|check_report_access|verify_signature|session_valid|clamp_limit|safe_upload_path|compare_digest|ALLOWED_",
                    "max_results": 40,
                },
            )
        ]
    )


def _diagnose_step2(request: LLMRequest) -> FakeTurn:
    finding = _blocks(request).get("finding") or {}
    paths = finding.get("affected_paths") or ["app.py"]
    symbol = finding.get("affected_symbol")
    spans = _read_files(request)
    cause_path = paths[0]
    if cause_path in spans:
        text = spans[cause_path]["content"]
        base = int(spans[cause_path].get("start_line", 1))
        if symbol:
            m = re.search(rf"def {re.escape(symbol)}\b", text)
            if m:
                ln = text[: m.start()].count("\n") + 1
                start, end = base + ln - 1, base + ln + 30
            else:
                start, end = base, base + min(200, text.count("\n"))
        else:
            start, end = base, base + min(200, text.count("\n"))
    else:
        start, end = 1, 60
    hits = _search_hits(request)
    related = sorted({h["path"] for h in hits if h.get("path") and h["path"] != cause_path})[:5]
    submit = {
        "root_cause_summary": f"{finding.get('weakness_class', 'defect')} introduced in {cause_path}"
        + (f" ({symbol})" if symbol else "")
        + " — the guard or validation present in sibling code is absent here.",
        "cause_class": "missing_check",
        "cause_paths": [cause_path],
        "cause_start_lines": [int(start)],
        "cause_end_lines": [int(end)],
        "cause_symbol": symbol,
        "violated_expectation_ids": list(finding.get("violated_expectation_ids") or [])[:6],
        "strategy": "Restore the removed guard/validation so this path matches its siblings; keep behavior otherwise identical.",
        "constraints": ["sibling routes keep their current checks", "no public API change"],
        "files_expected": [cause_path],
        "tests_must_fail_before": [
            f"{cause_path} exposes the weakness class {finding.get('weakness_class', '')}"
        ],
        "tests_must_pass_after": [
            "the restored guard rejects the violating request while legitimate access still succeeds"
        ],
        "related_sites": related,
        "d3fend_technique": None,
    }
    return FakeTurn(tool_calls=[("submit_diagnose_output", submit)])


# --------------------------------------------------------------------------- assembly


def all_scripts() -> dict[AgentName, list[FakeStep]]:
    """Deterministic scripts for the four agents the eval exercises.

    Remediation agents are intentionally absent: the eval declines approval,
    so Patch/Fortify/Verify never run; if they ever did, FakeLLM raises —
    that failure would be a harness bug worth surfacing, not hiding.
    """
    return {
        AgentName.SCOUT: [_scout_step0, _scout_step1, _scout_step2],
        AgentName.PROBE: [_probe_step0, _probe_step1, _probe_step2],
        AgentName.TRACE: [_trace_step0, _trace_step1],
        AgentName.DIAGNOSE: [_diagnose_step0, _diagnose_step1, _diagnose_step2],
    }
