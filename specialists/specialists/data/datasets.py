"""Per-agent datasets, built deterministically from the synthetic corpus and from pinned public
MITRE data (CWE, CAPEC, ATT&CK, D3FEND) shipped in ``standards/``.

Every builder returns ``{"train": [...], "val": [...], "test": [...]}`` of rows with the input
fields the model reads and a ``label`` (a string, or a list of strings for multi-label tasks).
Splits never share a code family (corpus tasks), a phrasing family (Probe) or an ATT&CK parent
technique / CWE (MITRE tasks), so test scores measure generalisation, not memorisation.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import random
import zlib
from collections import defaultdict
from typing import Any

from standards import catalog

from .corpus import GENERATOR_VERSION, SAFE, generate
from .evalcommits import scout_rows as evalcommit_rows
from .realdata import attack_descriptions, diversevul, nvd_cves

Split = dict[str, list[dict[str, Any]]]


def _splits() -> Split:
    return {"train": [], "val": [], "test": []}


def digest(split: Split) -> str:
    blob = json.dumps(split, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


# ----------------------------------------------------------------- Scout


def scout(test_family: int = 3) -> Split:
    """Code snippet → CWE class or "safe". Vulnerable and incomplete-fix variants are positives
    (an incomplete fix is still vulnerable), the minimal fix is the hard negative."""
    out = _splits()
    for r in generate(test_family=test_family):
        base = {"family": r["family"], "framework": r["framework"]}
        out[r["split"]].append({**base, "code": r["vulnerable"], "label": r["cwe"]})
        out[r["split"]].append({**base, "code": r["partial"], "label": r["cwe"]})
        out[r["split"]].append({**base, "code": r["fixed"], "label": SAFE})
    # Real functions (DiverseVul). Their split is pinned by commit id, independent of the
    # corpus fold, so cross-validation folds stay consistent; the held-out fold is always
    # bucket 0. Vulnerable functions outside our 12 classes are "vuln_other", never "safe".
    for r in diversevul():
        out[r["split"]].append(
            {"family": r["family"], "framework": "c", "code": r["code"], "label": r["label"]}
        )
    for r in evalcommit_rows():
        out[r["split"]].append(
            {"family": r["family"], "framework": r["framework"], "code": r["code"], "label": r["label"]}
        )
    return out


# ----------------------------------------------------------------- Diagnose / Patch


def diagnose(test_family: int = 3) -> Split:
    """Vulnerable code + weakness class + validation observation → root-cause class."""
    out = _splits()
    for r in generate(test_family=test_family):
        out[r["split"]].append(
            {
                "family": r["family"],
                "code": r["vulnerable"],
                "cwe": r["cwe"],
                "evidence": r["observation"],
                "label": r["cause"],
            }
        )
    return out


def patch(test_family: int = 3) -> Split:
    """Vulnerable code + weakness class → remediation strategy (what the minimal fix does)."""
    out = _splits()
    for r in generate(test_family=test_family):
        out[r["split"]].append(
            {
                "family": r["family"],
                "code": r["vulnerable"],
                "cwe": r["cwe"],
                "label": r["strategy"],
            }
        )
    return out


# ----------------------------------------------------------------- Probe

_PHRASINGS: list[list[str]] = [
    [
        "{route} may return a {res} without checking that the caller owns it",
        "{route} could let one user act on another user's {res}",
        "the {res} handler at {route} looks up by id only",
    ],
    [
        "{route} builds a query from request input",
        "the search behind {route} concatenates a parameter into SQL",
        "user-controlled text reaches the database string at {route}",
    ],
    [
        "{route} passes request data to a shell",
        "the handler at {route} runs an external program with request input",
        "a filename from the request is placed in a command line at {route}",
    ],
    [
        "{route} joins a request path into a filesystem path",
        "the file served by {route} is chosen by the client",
        "an archive or path parameter at {route} may escape its directory",
    ],
    [
        "{route} echoes input into HTML",
        "markup from a {res} is rendered without encoding at {route}",
        "the page at {route} writes a query parameter into a script context",
    ],
    [
        "{route} fetches a URL supplied by the client",
        "the server at {route} makes an outbound request to a user-provided address",
        "an import or preview feature at {route} downloads arbitrary URLs",
    ],
    [
        "a credential appears to be committed in the {res} module",
        "a signing key or password is written as a literal near {route}",
        "a default administrator login may exist",
    ],
    [
        "{route} deserializes client-provided bytes",
        "a cookie or body field at {route} is loaded with a native object loader",
        "the job spec posted to {route} is evaluated rather than parsed",
    ],
    [
        "passwords are stored with a fast digest",
        "the login path compares unsalted hashes",
        "the key-derivation work factor for passwords looks low",
    ],
    [
        "{route} accepts an amount without checking it against the remaining balance",
        "a quantity or page size at {route} has no lower or upper bound",
        "repeated partial operations at {route} may exceed the original total",
    ],
    [
        "{route} allows any origin with credentials",
        "the CORS policy reflects the request origin",
        "a loose origin pattern trusts look-alike domains",
    ],
    [
        "{route} has no role check for a privileged action",
        "the admin action at {route} is reachable by any signed-in user",
        "authorization at {route} relies on a client-sent role field",
    ],
]
_PROBE_CLASSES = [
    "WSTG-ATHZ-04",
    "WSTG-INPV-05",
    "WSTG-INPV-12",
    "WSTG-ATHZ-01",
    "WSTG-INPV-01",
    "WSTG-INPV-19",
    "WSTG-ATHN-02",
    "WSTG-INPV-11",
    "WSTG-CRYP-04",
    "WSTG-BUSL-01",
    "WSTG-CLNT-07",
    "WSTG-ATHZ-02",
]
_PROBE_CWE = {
    "WSTG-ATHZ-04": "CWE-639",
    "WSTG-INPV-05": "CWE-89",
    "WSTG-INPV-12": "CWE-78",
    "WSTG-ATHZ-01": "CWE-22",
    "WSTG-INPV-01": "CWE-79",
    "WSTG-INPV-19": "CWE-918",
    "WSTG-ATHN-02": "CWE-798",
    "WSTG-INPV-11": "CWE-502",
    "WSTG-CRYP-04": "CWE-916",
    "WSTG-BUSL-01": "CWE-1284",
    "WSTG-CLNT-07": "CWE-942",
    "WSTG-ATHZ-02": "CWE-862",
}
_RATIONALE = [
    "Sibling handlers use a shared dependency that this one skips.",
    "The cited span reads the value straight from the request.",
    "No validation appears between the request and the sink.",
    "Expectation says the guarantee must hold for every caller.",
    "A similar pattern was fixed elsewhere in this repository.",
    "",
]


def probe(seed: int = 4401) -> Split:
    """Hypothesis text (+ a possibly wrong or missing CWE) → OWASP WSTG validation strategy.

    Phrasing 0-1 → train, 2 → val; a disjoint phrasing family per class is reserved for test
    (the third template rewritten with a different route/resource vocabulary). 25% of rows carry
    a wrong CWE and 15% none, as Scout's CWE is itself a hypothesis.
    """
    rng = random.Random(seed)
    routes_a = [
        "/orders/{id}",
        "/api/invoices/{id}",
        "/files/{name}",
        "/search",
        "/admin/export",
        "/webhooks/test",
    ]
    routes_b = [
        "/v2/claims/{claim}",
        "/tenants/{t}/reports",
        "/upload/extract",
        "/render",
        "/billing/refunds",
        "/img",
    ]
    res_a, res_b = ["order", "invoice", "document"], ["claim", "shipment", "ledger entry"]
    all_cwes = sorted(set(_PROBE_CWE.values()))
    out = _splits()
    for label, phr in zip(_PROBE_CLASSES, _PHRASINGS, strict=True):
        for k, template in enumerate(phr):
            for split, routes, res in (
                ("train" if k < 2 else "val", routes_a, res_a),
                ("test", routes_b, res_b),
            ):
                if split == "test" and k != 2:
                    continue
                for _ in range(40):
                    text = template.format(route=rng.choice(routes), res=rng.choice(res))
                    text = f"{text}. {rng.choice(_RATIONALE)}".strip()
                    # Training-only augmentation: some hypotheses carry no class-specific wording, so
                    # the model must also learn from the CWE field (test rows are never altered).
                    if split == "train" and rng.random() < 0.3:
                        text = rng.choice(_RATIONALE) or "Possible weakness at the cited span."
                        if rng.random() < 0.6:
                            text = f"{text} Route {rng.choice(routes)}."
                        out[split].append(
                            {
                                "family": f"{label}:{k}:generic",
                                "hypothesis": text,
                                "cwe": _PROBE_CWE[label],
                                "label": label,
                            }
                        )
                        continue
                    roll = rng.random()
                    cwe = (
                        ""
                        if roll < 0.15
                        else rng.choice([c for c in all_cwes if c != _PROBE_CWE[label]])
                        if roll < 0.40
                        else _PROBE_CWE[label]
                    )
                    out[split].append(
                        {"family": f"{label}:{k}", "hypothesis": text, "cwe": cwe, "label": label}
                    )
    return out


PROBE_CWE_TO_STRATEGY = {v: k for k, v in _PROBE_CWE.items()}

# ----------------------------------------------------------------- Verify


def _udiff(a: str, b: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"a/{path}", f"b/{path}", n=1)
    )


_COSMETIC = [("    return", "    # reviewed\n    return"), ("(", "( "), ("\n", "\n\n")]


def verify(seed: int = 5502, test_family: int = 3) -> Split:
    """Candidate patch (diff) + executed test summary → evidence verdict class (advisory).

    adequate: the minimal fix with a regression test that failed before and passes after.
    incomplete_fix: the incomplete variant; the regression test still passes (a weak test).
    test_weakened: the fix, but the diff also deletes or weakens an existing assertion.
    no_security_change: a cosmetic edit that leaves the vulnerable code in place.
    """
    rng = random.Random(seed)
    out = _splits()
    for r in generate(test_family=test_family):
        path = rng.choice(
            ["app/routes.py", "src/routes/index.js", "api/handlers.py", "server/app.js"]
        )
        test = f"test_{r['family']}_regression"
        before_after = f"{test}: failed before the patch, passed after. Full suite: {rng.randint(18, 90)} passed, 0 failed."
        weak = f"{test}: passed before the patch, passed after. Full suite: {rng.randint(18, 90)} passed, 0 failed."
        cases = {
            "adequate": (_udiff(r["vulnerable"], r["fixed"], path), before_after),
            "incomplete_fix": (_udiff(r["vulnerable"], r["partial"], path), weak),
        }
        assertion = rng.choice(
            [
                "assert resp.status_code == 404",
                "expect(res.status).toBe(403)",
                "assert 'error' in body",
            ]
        )
        removed = f"--- a/tests/test_api.py\n+++ b/tests/test_api.py\n@@ -40,3 +40,2 @@\n     resp = client.get(url)\n-    {assertion}\n"
        if rng.random() < 0.5:
            removed = '--- a/tests/test_api.py\n+++ b/tests/test_api.py\n@@ -12,2 +12,2 @@\n-@pytest.mark.security\n+@pytest.mark.skip(reason="flaky")\n def test_access():\n'
        cases["test_weakened"] = (_udiff(r["vulnerable"], r["fixed"], path) + removed, before_after)
        old, new = rng.choice(_COSMETIC)
        cosmetic = r["vulnerable"].replace(old, new, 1)
        if cosmetic == r["vulnerable"]:
            cosmetic = r["vulnerable"] + "\n"
        cases["no_security_change"] = (_udiff(r["vulnerable"], cosmetic, path), weak)
        # A good patch usually adds its regression test; incomplete and cosmetic ones sometimes do
        # too. Without this, "touches a test file" alone would mean "weakened a test".
        lang_py = r["framework"] != "express"
        added = (
            (
                f"--- /dev/null\n+++ b/tests/{test}.py\n@@ -0,0 +1,4 @@\n+def {test}(client, other_user):\n"
                f"+    resp = client.get(url, headers=other_user)\n+    assert resp.status_code in (403, 404)\n+\n"
            )
            if lang_py
            else (
                f"--- /dev/null\n+++ b/test/{test}.test.js\n@@ -0,0 +1,4 @@\n+test('{test}', async () => {{\n"
                f"+  const res = await request(app).get(url).set(otherUser);\n+  expect([403, 404]).toContain(res.status);\n+}});\n"
            )
        )
        for label in ("adequate", "incomplete_fix", "no_security_change", "test_weakened"):
            p_add = {
                "adequate": 0.7,
                "incomplete_fix": 0.5,
                "no_security_change": 0.3,
                "test_weakened": 0.3,
            }[label]
            if rng.random() < p_add:
                diff, tests = cases[label]
                cases[label] = (diff + added, tests)
        for label, (diff, tests) in cases.items():
            out[r["split"]].append(
                {
                    "family": r["family"],
                    "diff": diff,
                    "tests": tests,
                    "cwe": r["cwe"],
                    "label": label,
                }
            )
    return out


# ----------------------------------------------------------------- Trace (MITRE CAPEC → ATT&CK)

TACTICS = [
    "initial-access",
    "execution",
    "persistence",
    "privilege-escalation",
    "defense-evasion",
    "credential-access",
    "discovery",
    "lateral-movement",
    "collection",
    "command-and-control",
    "exfiltration",
    "impact",
]


def _bucket(key: str, mod: int) -> int:
    return zlib.crc32(key.encode()) % mod


def trace() -> Split:
    """CWE (name) → ATT&CK tactics reachable through CAPEC attack patterns (multi-label).

    Labels come from the pinned MITRE catalogs: CWE ← CAPEC.related_weaknesses, CAPEC →
    ATT&CK technique → tactics. Inputs are the CWE's name and the names of its CAPEC patterns'
    *weakness* siblings are NOT used (that would leak); only the CWE name. Split by CWE id.
    """
    capec = catalog.load_json("capec.json")["patterns"]
    attack = catalog.load_json("attack-enterprise.json")["techniques"]
    cwe = catalog.load_json("cwe.json")["entries"]
    tactics: dict[str, set[str]] = defaultdict(set)
    for pat in capec.values():
        ts = {
            t
            for tech in pat.get("attack", [])
            for t in attack.get(tech.split(".")[0], attack.get(tech, {})).get("tactics", [])
        }
        for w in pat.get("cwe", []):
            tactics[w] |= ts & set(TACTICS)
    out = _splits()
    for cid, labels in sorted(tactics.items()):
        entry = cwe.get(cid)
        if not labels or not entry or entry.get("status") == "Deprecated":
            continue
        split = {0: "test", 1: "val"}.get(_bucket(cid, 6), "train")
        out[split].append(
            {"family": cid, "cwe": cid, "name": entry["name"], "label": sorted(labels)}
        )
    # Real CVE descriptions as extra "name"-field inputs (NVD). A CVE's rows land in the
    # same split as its CWE, so no weakness appears on both sides.
    for cve in nvd_cves():
        labels = set()
        for cid in cve["cwe"]:
            labels |= tactics.get(cid, set())
        if not labels:
            continue
        primary = cve["cwe"][0]
        split = {0: "test", 1: "val"}.get(_bucket(primary, 6), "train")
        out[split].append(
            {"family": primary, "cwe": primary, "name": cve["desc"][:1500], "label": sorted(labels)}
        )
    return out


# ----------------------------------------------------------------- Fortify (MITRE ATT&CK → D3FEND)


def fortify() -> Split:
    """ATT&CK technique (name + tactics) → D3FEND countermeasures (multi-label).

    Labels: the pinned ATT&CK→D3FEND mapping. Only D3FEND techniques that appear for at least
    15 ATT&CK techniques are kept as labels. Split by *parent* technique, so T1003.001 and
    T1003.002 never sit on both sides.
    """
    mapping = catalog.load_json("attack-d3fend.json")
    attack = catalog.load_json("attack-enterprise.json")["techniques"]
    counts: dict[str, int] = defaultdict(int)
    for ids in mapping.values():
        for d in ids:
            counts[d] += 1
    keep = {d for d, n in counts.items() if n >= 10}
    out = _splits()
    for tid, ids in sorted(mapping.items()):
        labels = sorted(set(ids) & keep)
        tech = attack.get(tid)
        if not labels or not tech:
            continue
        parent = tid.split(".")[0]
        name = tech["name"]
        if "." in tid and parent in attack:
            name = f"{attack[parent]['name']}: {name}"
        split = {0: "test", 1: "val"}.get(_bucket(parent, 6), "train")
        out[split].append(
            {
                "family": parent,
                "technique": tid,
                "name": name,
                "tactics": " ".join(tech.get("tactics", [])),
                "label": labels,
            }
        )
    # ATT&CK STIX descriptions as additional "name"-field rows with the same labels: the
    # model sees real technique wording, not only titles. Same split as the technique.
    descs = attack_descriptions()
    for tid, ids in sorted(mapping.items()):
        labels = sorted(set(ids) & keep)
        tech = attack.get(tid)
        if not labels or not tech or tid not in descs:
            continue
        parent = tid.split(".")[0]
        split = {0: "test", 1: "val"}.get(_bucket(parent, 6), "train")
        out[split].append(
            {
                "family": parent,
                "technique": tid,
                "name": descs[tid][:1500],
                "tactics": " ".join(tech.get("tactics", [])),
                "label": labels,
            }
        )
    return out


CORPUS_TASKS = ("scout", "diagnose", "patch", "verify")
BUILDERS = {
    "scout": scout,
    "probe": probe,
    "trace": trace,
    "diagnose": diagnose,
    "patch": patch,
    "fortify": fortify,
    "verify": verify,
}
DATASET_VERSION = f"{GENERATOR_VERSION}+mitre-pinned+real-corpus"
