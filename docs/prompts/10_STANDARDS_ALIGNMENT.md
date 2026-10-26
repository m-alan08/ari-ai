# Implementation prompt: standards alignment (ATT&CK, D3FEND, NIST SP 800-61, CVSS, CWE, OWASP LLM Top 10)

> Paste this into a fresh coding-agent session at the repo root. It is self-contained,
> but read `CLAUDE.md`, `AGENTS.md`, `MASTER_BUILD_SPEC.md` and `backend/INTEGRATION.md` first.
> It is a **development** prompt. It is not a runtime agent prompt.

## 0. Mission and non-negotiables

Make every security result the platform produces traceable to six industry standards:

* MITRE ATT&CK techniques and MITRE D3FEND countermeasures;
* NIST SP 800-61 incident-handling phases;
* CVSS scores and CWE weakness IDs;
* an evidence-backed coverage matrix for the OWASP Top 10 for LLM Applications.

All of it must be backed by executed evidence, the same way findings already are.

Rules that override convenience:

1. **Identifiers come from official data, never from a model.** Vendor the official
   machine-readable catalogs, pin each to a version and SHA-256, and load them offline.
   Every ID the system stores (`T1078`, `D3-NI`, `CWE-639`, `CAPEC-…`, `LLM01:2025`) must
   resolve in a catalog, or be rejected with `SCHEMA_INVALID`. An LLM may *propose* a
   mapping; deterministic code validates it and records how it was derived (`mapped_via`).
2. **Scores are computed, not generated.** CVSS base scores come from a reference
   implementation applied to metric values. The LLM never writes the numeric score.
3. **Additive contracts only.** Shared models change through `shared/` (contract owner:
   Srihith). This is a minor version bump (the current `SCHEMA_VERSION` is 0.5.0, so 0.6.0).
   Existing fields keep their meaning: `SeverityAssessment.level` stays, and CVSS is added
   alongside it. Regenerate `shared/generated` and the fixtures, and send a
   `coordination/messages/` note.
4. **Honesty labels.** Mappings are classifications with confidence and provenance, not
   claims of observed adversary behavior. Simulated and RL-derived items stay labeled
   `simulation_derived`. Never write "detected ATT&CK technique X in production".
5. **Licensing.** Before vendoring, read each source's terms (ATT&CK and D3FEND terms of
   use, the CWE/CAPEC terms, FIRST's CVSS specification terms, OWASP's licence). Record the
   decision, attribution text and versions in `docs/THIRD_PARTY_DATA.md` and link it from
   `LICENSE-DECISION.md`.
6. **No network at runtime.** Catalogs are files in the repo, or fetched by an explicit
   `make standards-data` step that verifies the SHA-256. Tests run offline.
7. **Versions must be checked.** Verify the current published versions before pinning, at
   minimum CVSS v4.0, NIST SP 800-61 Rev. 3 (April 2025), and OWASP LLM Top 10 2025. If a
   newer revision exists, use it and say so in the PR.

## 1. Current state (verified 2026-10-17 against `main` @ ea36e47; re-check before starting)

| Standard | Status | Where |
|---|---|---|
| CWE | **Partial.** `Hypothesis.weakness_class` (shared) and the security_agents/code_review models enforce the format `^CWE-\d{1,5}$`, but **IDs are not validated against the CWE list**. `Finding` has no CWE field. Functional findings carry none. | `shared/shared/findings.py`, `security-agents/security_agents/agents/{scout,fortify}.py`, `code-review/code_review/models.py` |
| CVSS | **Not implemented.** `SeverityAssessment{level, factors{reachability, privileges_required, data_sensitivity, verified_path}}` is a custom "CVSS-like" rubric (docs/07). RQ-016 is open. | `shared/shared/findings.py`, `security-agents/.../agents/trace.py` |
| MITRE ATT&CK | **Not implemented.** | none |
| MITRE D3FEND | **Not implemented.** Related data exists: `HardeningRecommendation.category` (Fortify) and RL `DefenseActionName` (`isolate`, `rate_limit`, `raise_monitoring`, …). | `shared/shared/findings.py`, `shared/shared/rl.py` |
| NIST SP 800-61 | **Not implemented.** No incident entity or lifecycle. Related data exists: runtime `log_anomaly` MonitoringEvents, RL DefenseRecommendations, findings, approvals and the audit chain. | `backend/backend/api/runtime.py`, `monitoring/`, `rl-defense/` |
| OWASP LLM Top 10 | **Not mapped.** Relevant controls exist with no coverage matrix: untrusted-data blocks, tool allowlists and `scope_guard`, redaction, budgets, structured-output validation, Verify independence, approval gates, strict tool schemas. | `backend/backend/llm.py`, `security-agents/...`, `code-review/...` |

## 2. Deliverables

### 2.1 Vendored catalogs: `standards/` (new top-level package `standards`, uv workspace member)

* `standards/data/`: pinned official files, for example the ATT&CK Enterprise STIX bundle, the
  D3FEND ontology plus its official ATT&CK mappings, the CWE XML list, CAPEC (which provides
  CWE ↔ CAPEC ↔ ATT&CK links), and the OWASP LLM Top 10 entries (IDs and titles only; reference
  the text by URL). Each entry in `standards/data/MANIFEST.json` records the version, source URL,
  SHA-256 and licence.
* `standards/standards/catalog.py`: a read-only, cached loader with these functions:
  `attack_technique(id)`, `d3fend_technique(id)`, `cwe(id)`, `capec(id)`, `owasp_llm(id)`,
  `capec_for_cwe(cwe)`, `attack_for_capec(capec)`, `d3fend_for_attack(technique)`.
  Unknown IDs raise `KeyError`. The loader exposes `catalog_versions()`.
* `standards/standards/cvss.py`: CVSS **v4.0** base scoring through a maintained reference
  library. Evaluate the `cvss` package (RedHat) and pin it, or use FIRST's published
  calculator logic with its test vectors. Write `vector(metrics) -> str`,
  `score(vector) -> (float, severity)`, and `from_factors(SeverityFactors, …) ->
  CVSSv4Metrics`, a deterministic derivation documented as a table. Write each AV/AC/AT/
  PR/UI/VC/VI/VA/SC/SI/SA choice down with the evidence it rests on. Where the evidence can't
  determine a metric, choose the conservative value and set `assumed=True` on it.

### 2.2 Shared contracts, additive 0.6.0 (`shared/shared/standards.py` + additions)

```python
class StandardRef(StrictModel):  # one validated identifier with provenance
    framework: Literal["attack", "d3fend", "cwe", "capec", "owasp_llm", "nist_800_61"]
    id: ShortText  # e.g. "T1078", "D3-NI", "CWE-639", "LLM01:2025"
    name: ShortText  # from the catalog, never model text
    catalog_version: ShortText
    mapped_via: Literal["direct", "cwe_capec_attack", "attack_d3fend", "rule", "human"]
    confidence: Confidence
    evidence: list[EvidenceRef] = []


class CVSSAssessment(StrictModel):
    version: Literal["4.0"]
    vector: str  # validated CVSS:4.0/AV:.../... string
    base_score: float = Field(ge=0, le=10)
    severity: Literal["none", "low", "medium", "high", "critical"]
    assumed_metrics: list[ShortText] = []  # metrics not determined by evidence
    rationale: ShortText
```

Changes to existing models, all optional with defaults:

* `Finding`: add `cwe: StandardRef | None`, `attack: list[StandardRef] = []`,
  `cvss: CVSSAssessment | None`.
* `SeverityAssessment`: add `cvss: CVSSAssessment | None`.
* `AttackPath.steps[*]` (`PathStep`): add `attack: list[StandardRef] = []`.
* `HardeningRecommendation`: add `d3fend: list[StandardRef] = []`.
* `DefenseRecommendation` (RL): add `d3fend: list[StandardRef] = []`.

New incident entity (`shared/shared/incident.py`), NIST SP 800-61 Rev. 3, organized around
the CSF 2.0 Respond and Recover functions. Verify the phase and function names against the
published document, and keep a mapping table to the Rev. 2 lifecycle as well.

```python
class IncidentPhase(StrEnum):  # Rev.2 lifecycle labels kept for familiarity, CSF function in data
    detection_analysis = "detection_analysis"
    containment = "containment"
    eradication = "eradication"
    recovery = "recovery"
    post_incident = "post_incident"


class Incident(StrictModel):
    id: IncidentId  # new id prefix "inc"
    project_id: ProjectId
    title: ShortText
    phase: IncidentPhase
    csf_function: Literal["DE", "RS", "RC", "ID", "PR", "GV"]
    severity: SeverityLevel
    simulated: bool  # true when built only from demo/replay/simulated signals
    monitoring_event_ids: list[MonitoringEventId]
    finding_ids: list[FindingId]
    recommendation_ids: list[RecommendationId]  # RL, simulation-derived
    approval_ids: list[ApprovalId]
    attack: list[StandardRef] = []
    timeline: list[IncidentTimelineEntry]  # who/what/when, linked to audit events
    lessons_learned: ShortText | None = None  # filled in post_incident, cites regression tests
    opened_at: UtcDatetime
    closed_at: UtcDatetime | None = None
```

Add the incident's allowed phase transitions to the shared state tables, with tests, the same
way assessment states are defined.

### 2.3 Producers (owners in parentheses; the user has authorized fixing teammates' code while they are away, but announce changes in `coordination/messages/`)

* **Security agents (Alan), `security-agents/`:**
  * Scout and Probe: validate `weakness_class` against the CWE catalog. Reject unknown IDs with
    `SCHEMA_INVALID` and report them back to the model, as tool errors are today.
  * Trace: tag attack-path steps and findings with ATT&CK techniques through
    `CWE → CAPEC → ATT&CK` from the catalog (`mapped_via="cwe_capec_attack"`), and compute
    `CVSSAssessment` deterministically from the severity factors and validated evidence.
  * Fortify: tag hardening recommendations with D3FEND countermeasures through
    `ATT&CK → D3FEND` (official mappings) plus a reviewed rule table from Fortify category to
    D3FEND, kept in `standards/data/rules/fortify_d3fend.yaml` with a justification per row.
  * Export all of it through `contracts/export.py` into the shared models.
* **Reviewer (Sai), `code-review/`:** functional findings get a CWE when one genuinely fits
  (e.g. business-logic weaknesses in the CWE-840 family), validated against the catalog. Leave
  it `None` rather than forcing a bad mapping.
* **RL (Adam), `rl-defense/`:** a rule table mapping each `DefenseActionName` to D3FEND
  countermeasures (for example `isolate` → network isolation). Verify the exact D3FEND IDs in
  the catalog and justify each row. The output stays `simulation_derived=True`.
* **Monitoring and backend (Srihith):**
  * Incident service: open an `Incident` (phase `detection_analysis`, CSF DE) when a
    `log_anomaly` event crosses the threshold on a route with open validated findings, or when
    an owner opens one manually. Link the events, findings and RL recommendations.
  * Phase transitions happen only through API actions with approvals: containment, then
    eradication (an approved remediation plus an accepted Verify), then recovery, then
    post_incident (lessons learned citing regression tests from Fortify).
  * Every transition writes the audit chain. Containment and eradication are
    **recommendations plus human-approved actions**. They are never executed automatically
    against real systems.
  * API: `GET/POST /api/v1/projects/{id}/incidents`, `GET /incidents/{id}`,
    `POST /incidents/{id}/transition` (CSRF, role checks, approval rules), plus incident events
    on SSE. Include a migration.
  * Reports: add `standards` sections to `/assessments/{id}/report` and to the incident detail
    (ATT&CK, D3FEND, CWE and CVSS tables, all from persisted records).
* **Frontend (Vihaan), `frontend/`:** show the standards tags with catalog names and
  versions, a CVSS vector tooltip with the computed score, and an incident timeline with the
  current phase and CSF function. Label simulated incidents and simulation-derived
  countermeasures visibly.

### 2.4 OWASP LLM Top 10 coverage (platform self-assessment)

Write `docs/security/OWASP_LLM_TOP10_COVERAGE.md`: one row per OWASP LLM risk (2025 list,
LLM01–LLM10). Verify the titles against the published list. Each row names the control in
this codebase (file and function), the **executable test** that proves it, and the residual
risk. Back it with `tests/security/test_owasp_llm_controls.py`, which collects and runs those
tests by marker (`@pytest.mark.owasp_llm("LLM01")`). A risk with no executable control is
listed as a **gap**, not as covered. Candidate existing controls to map, after confirming each
one:

* untrusted-data framing in tool results and context packs (LLM01);
* redaction before persistence and model calls (LLM02);
* pinned dependencies and vendored catalogs with SHA-256 (LLM03);
* structured-output validation and Verify independence (LLM05);
* per-agent tool allowlists, `scope_guard` and approval gates (LLM06);
* budgets and turn limits (LLM10).

### 2.5 Tests (all offline and deterministic; live checks opt-in)

* **Catalog integrity:** the manifest SHA-256 matches; known IDs resolve; an invented ID such as `T9999` is rejected.
* **CVSS:** reproduce FIRST's published CVSS v4.0 test vectors exactly, and check that `from_factors` is deterministic, with assumed metrics listed.
* **Mappings:** IDOR/`CWE-639` on payments-lite maps to the expected CAPEC/ATT&CK chain, as given by the catalog data. Fortify and RL rule tables only reference IDs that exist in the catalog.
* **Contracts:** fixtures round-trip, generated JSON Schema and TypeScript are fresh, and the TypeScript compiles.
* **Incident lifecycle:**
  * illegal transitions return `CONFLICT_STATE`;
  * containment and eradication need approvals;
  * eradication needs an accepted VerificationResult;
  * the audit chain verifies;
  * SSE carries incident events;
  * simulated inputs produce `simulated=true`.
* **End to end:** extend `backend/tests/test_unified_assessment.py` (host mode, scripted turns) so the payments-lite assessment produces:
  * a security finding with a validated CWE, ATT&CK refs, a computed CVSS vector and score, and a Fortify recommendation with D3FEND refs;
  * a runtime anomaly on the same route that opens an Incident linking that finding, which can be moved through the phases by API calls with approvals.
* **OWASP:** the coverage test fails if any matrix row claims "covered" without a passing marked test.

## 3. Acceptance gate (record exact commands, SHAs and counts in `coordination/status/`)

* `uv run pytest -q` (root), `cd security-agents && uv run pytest -q`, `cd code-review && uv run pytest -q`
  and the rl-defense suite are green. `uv run mypy` and ruff are clean, and all CI workflows pass.
* `python -m shared.export --check` and the fixture checks pass. A coordination message lists
  every contract addition.
* No identifier, score or mapping in persisted records or the UI comes from free model text
  without catalog validation (add a test that asserts this on the e2e assessment).
* `docs/THIRD_PARTY_DATA.md` records each source's version, SHA-256, licence and attribution.
* Optional live check (needs explicit approval, because it costs money): one unified live run
  (`make smoke-live`-style, $5 cap) showing standards tags on real findings.

## 4. Out of scope / do not

* Do not claim detection of real-world ATT&CK activity, and do not execute containment against real systems.
* Do not scrape the standards websites at runtime, and do not hand-type catalog contents.
* Do not replace `SeverityAssessment.level`. Add CVSS alongside it and document how the two relate.
