# RC4 NO-GO remediation — 2026-10-25

Independent RC4 review: https://github.com/adamissac/ari-ai/pull/632
(`codex/rc4-independent-audit`, `docs/reviews/rc4-independent/REPORT.md`)
Verdict there: **NO-GO** on `6a72949efa7c0a2d3233eb6884bb7a23dfe71ced` — new P2
finding N8 only; F1–F10 and N1–N7 all PASS.

## Candidate after remediation

Integration tip: **`2f2f4bc38e87ade6c2ba0f4e86f466d3ced18e93`** on
`integration/2026-11-02-release` (PR https://github.com/m-alan08/ari-ai/pull/8,
base `ci/upstream-e41c9dc2` = canonical main
`e41c9dc22e6536463cdab9be6a2a4cd76302c866`).

Prior candidate `6a72949e` and frozen RC1 `bc490484` preserved.

## N8 — P2 artifact disclosure (owner fix `5628af35`)

`_CODE_KV_PATTERN` missed two code forms, so quoted secrets survived in diff
artifacts labelled `redacted=true` and were served to project viewers:

- quoted mapping keys / subscripts: `{"password": "lit"}`, `d["k"] = "lit"`
- annotated assignments: `password: str = "lit"`

Owner: execution-reliability session (also owns `redaction.py`).
Fix (their commit `5628af35` on `feat/devin-execution-reliability`, merged here
as `76ee39f5`): `_CODE_KV_PATTERN` gained literal-terminating prefixes for
annotated assignments and quoted mapping keys/subscripts (single + double
quotes). Only the quoted literal is rewritten; unquoted non-secret expressions
(`api_key=os.environ["API_KEY"]`, `password: str = os.getenv(...)`) remain
byte-identical — N7 is not reopened. Sealed-diff path untouched; unrelated-user
404 preserved; `redacted=true` labelling unchanged.

Codex's verbatim probes (copied as
`backend/tests/live_defense_e2e/test_rc4_n8_artifact_disclosure.py` @ `4cd32dfb`)
— **4/4 pass**: ArtifactStore.put_text/get roundtrip redacts the literal while
keeping `redacted` semantics; the real viewer API returns 200 without the
secret; the unrelated user still gets 404. N7 probes (test_redact_code /
test_redact_text_gaps, vendored + suite) re-run green.

## Upstream reconciliation — canonical main `e41c9dc2`

Canonical main moved `eea084bc` → `e41c9dc2` (~25 PRs). Merged into the
integration branch at `463210f8`. Preserved upstream semantics, verified by
probe adaptation rather than weakened assertions:

- #607 split user rate limits: `rate_limit_read_per_minute=600` for GET/HEAD,
  `rate_limit_per_minute` for writes — `require_user` applies both; adapted
  `test_customer_security_hardening.py` to the split contract.
- #607 manual-only quota counting: `check_project_quota(manual=True)` filters
  `trigger == manual`; adapted `test_rc2_latest_quota.py` (manual-only seeds,
  429 `RATE_LIMITED` + `resets_at`).
- #593 `ReviewerSettings` + reviewer author source `pull_request.user.login`;
  PR probe payload carries `user.login`.
- #598 `GET /api/v1/github/commit`; #616 stale-base delivery; #621
  retention/destructive-pruning guards; #601/#618/#626 verify protections
  (`VerifyEvidence(candidate_id=…)` seam resolves in the merged tree — upstream
  `verify.py` defines the field, `orchestrator.py` passes it).
- Frontend #619–#624 land via the merge.

## Transfer stack — rebuilt on `e41c9dc2`, N8 cascaded

| Step | Branch | SHA | Schema |
|---|---|---|---|
| 1 | `transfer/01-pr412` | `8a945e04b9cecb3d9f10912f78d160f2673594e0` | 0.22 |
| 2 | `transfer/02-pr458` | `93b4fe82b8f12df50486b2f452d9d0d339e93bee` | 0.23 |
| 3 | `transfer/execution-reliability` | `5e130915d30aad375adc9750f2232e2d29c085cb` | 0.23 |
| 4 | `transfer/investigation-observability` | `660a3575d9eaf0a2df67a50002b2ec924f182f23` | 0.24 |
| 5 | `transfer/pilot-security-hardening` | `89a29d170d4cf1f589975713ee7c8ada778f1ab6` | 0.25 |
| 6 | `transfer/customer-onboarding` | `10e923d1ffda75b19b848a7154f8ba867a0a8589` | 0.26 |
| 7 | `transfer/pilot-deployment-ops` | `c1959b4be4dbaa2b25d26e858a5219802f320d0c` | 0.26 |
| 8 | `transfer/release-validation` | `b2a10667b6757e67fb32323919ef2d3cf843160a` | 0.26 |

All merges forward-only (no force-push). Cascade: N8 fix merged at S3, then
S4→S8 re-merged in order; `test_rc2_transfer_manifest.py` repinned (`7c78f3a4`)
and green.

**Merge equivalence:** `git merge-tree --write-tree` synthetic composition of
the eight steps equals the actual stack trees on `e41c9dc2`; S8's tree diff vs
the candidate is documentation/test-results/coordination files only — zero
production, test, or contract differences. Migrations single head `0019`
(upstream 0016/0017, 0018 execution_reliability, 0019 repository_disconnect).
S8 generated contracts byte-identical to the candidate's regenerated files.
Schema progression 0.22→0.26 monotonic (Codex N6 assertion unchanged, passing).

## Strict matrix on `2f2f4bc3`

- backend: see "Local matrix" line in the PR comment (full suite re-run on the
  final tree after the N8 merge)
- live_defense_e2e + N1–N8 + F1–F10 + R2–R5 + manifest/independent-review:
  **58 passed, 1 skipped** (opt-in live provider eval)
- security-agents: **795 passed, 24 skipped**
- shared: 13 passed, 3 skipped · migrations 2/2, head `0019`
- frontend: **403 passed** + lint + typecheck + build clean
- transport: 36/36
- `make check` + `mypy`: clean (41 source files, 0 issues)
- zero xfails; no assertion weakened (regressions fixed by adapting probe
  payloads/settings to real upstream contract changes)

## GitHub CI

PR #8 base retargeted to `ci/upstream-e41c9dc2` so `pull_request` workflows run
on the actual upstream merge commit (ci/upstream-main is protected; a new
mirror branch was created instead of a merge). Latest CI runs execute against
the real candidate head. See the PR checks section for same-SHA results.

## Outstanding blockers

1. Codex exact-SHA review of `2f2f4bc3` (or the next tip — docs commits only).
2. Adam's approval of the upstream landing order S1→S8.
3. `OPENAI_API_KEY` + `ARI_FIX_WEBHOOKS` fork secrets + 7 findings-router
   automation approvals (pipeline otherwise degrades to CI-only NO-GO).
4. `pilot-verify` manual dispatch; ops items (real secrets, backup schedule,
   staging deploy + `--remote` readiness).

## Verdict

**GO FOR INDEPENDENT REVIEW** — not customer GO. Nothing merged, deployed, or
enforcement-enabled.

---

# RC5 addendum — N9 statement-boundary fix (2026-10-25, later)

Codex's repeat independent review of `4fdcf10c` (upstream PR #633,
`docs/reviews/rc4-n8-independent/REPORT.md`) returned NO-GO on a single new
P2: **N9** — the annotated-assignment alternative added by the N8 fix used
`[^=\n]+?` for the type annotation, letting it cross `;` and redact an
unrelated statement (`password: str; notice = "public notice"` →
`notice = "[REDACTED:kv]"`). F1–F10, N1–N8, R2–R5, upstream composition and
the split read/write limit control all passed.

## N9 fix

- Owner: reliability/redaction session (`devin-1dc1161e34044bc882cba44931c122b3`),
  commit `231b1364f6d72847711fc4cfa7fa3f78f376bf1f` on
  `feat/devin-execution-reliability` (fork PR #1). One character class:
  `[^=\n]+?` → `[^=\n;#]+?` — the annotation span can no longer consume `;`
  or `#` into another statement; `password: str == "x"` comparisons untouched.
- Merged into the candidate: `bae66e01684e83f565a80bb21de75b0c3b168e81`.
- Cascade: S3→S8 re-merged in order (forward-only). Stack tips after N9:
  S1 `8a945e04` · S2 `93b4fe82` · S3 `b8ccb7a8` · S4 `19b97702` ·
  S5 `87fd7149` · S6 `e6b7a326` · S7 `25904496` · S8 `1db36aed`.
- `test_rc2_transfer_manifest.py` repinned to the N9-cascaded tips
  (`0f97a901`); S8 re-aligned to the candidate after lint cleanup
  (`1db36aed`) — zero diffs in `backend/ shared/ frontend/ security-agents/
  .github/`.
- Lint/style pass on the final tip: `86a5f846` (ruff format + E741/F541 in
  `.github/release-review/route_findings.py`).

## Verification on the final tree

- Codex N9 probes (verbatim from PR #633 `test_additional.py`, committed as
  `backend/tests/live_defense_e2e/test_rc4_n9_statement_boundaries.py`):
  3/3 — byte-identical `redact_code` on the offending line, byte-identical
  ArtifactStore diff roundtrip, split-limit positive control.
- N8 probes 4/4, N7 probes 4/4 re-run; manifest + independent-review tests pass.
- Full `backend/tests` on `86a5f846`: **843 passed, 9 skipped, 0 failed**
  (includes all N1–N9, F1–F10, R2–R5).
- GitHub CI on `86a5f846`: strict-regressions + all suite jobs green except
  the opt-in paid `live` job (skipped by design); the pipeline's posted audit
  remains NO-GO solely because `OPENAI_API_KEY` is not configured on the fork.
- Codex's third CI "failure" (retained legacy
  `test_global_user_limit_applies_on_all_routers`) is a stale test asserting
  the pre-#607 single-limit contract; the adapted
  `rate_limit_read_per_minute` variant is the correct expectation per upstream
  #607, and Codex's own split-limit control passes.

## Outstanding blockers (unchanged except the SHA)

1. Codex exact-SHA review of **`86a5f8464b2855f0f3e014e9616e254bc7bdf8f3`**
   (integration tip `1ef6d325` adds the pipeline report commit, docs only).
2–4. Same as above (Adam's S1→S8 sign-off; fork secrets + automation
   approvals; ops items).

## Verdict (RC5)

**GO FOR INDEPENDENT REVIEW** on `86a5f846` — not customer GO. Nothing merged,
deployed, or enforcement-enabled.

---

## RC5 final addendum — canonical `edfa9b8b` incorporated (#629 reconciliation)

Codex's RC5 check (upstream PR #639) confirmed N9 fixed and all original
controls passing, but found the reviewed candidate predated upstream #629.
Current canonical main `edfa9b8b` is now incorporated into the candidate and
the full verification matrix re-executed on the merged tree.

**Upstream range incorporated:** `e41c9dc2..edfa9b8b`
- `59cd0b62` #629 — `fix(verify): detect hook attribute writes and symlinked
  rootdir config changes` (security-agents `tools/services.py` +214,
  `tests/test_e2e_replay.py` +379, README +19). Composes cleanly: Codex's
  "192 pass / 2 fail" was the pre-merge tree running the new upstream tests;
  post-merge all 59 hook/rootdir/symlink tests pass with no adaptation.
- `2b87e32c` #630 — frontend sandbox scope duration (console page, setup,
  `lib/real-assessment.ts` + unit tests).
- `7df9f655` #632, `1639bab0` #633 — review docs. `c5bc50a4` + `edfa9b8b`
  (#637) — handoff docs.
- Only conflict across integration + all 8 stack branches:
  `docs/planning/SESSION_HANDOFF.md` — union-resolved per convention
  (pure additions on both sides). **No production-code conflict existed.**

**Merge commits:** integration `53bd8d0f`; stack S1 `8f2390f3`,
S2 `27077358`, S3 `07fcec56`, S4 `e726c2d5`, S5 `e7a35cc1`, S6 `d4da9f43`,
S7 `d3c0da49`, S8 `34261c36` (+ S8 workflow alignment `e04ce68d`).

**Gate/pipeline:** PR #8 base retargeted to `ci/upstream-edfa9b8b` (exact
mirror of canonical `edfa9b8b`); review-gate `base_sha` now resolves from the
PR's `base.sha` via API so future retargets stay correct (`e019eff0`).

**Transfer stack — all eight steps on canonical `edfa9b8b`, merge-only:**

| Step | Branch | Tip |
|---|---|---|
| 1 | `transfer/01-pr412` | `8f2390f3a0113afa70e0e6aaac3c28c1ada1c1d7` |
| 2 | `transfer/02-pr458` | `27077358fe6b906269025f4d77f8285172e4772a` |
| 3 | `transfer/execution-reliability` | `07fcec56372badad357114feb6a3c42d1ca5734c` |
| 4 | `transfer/investigation-observability` | `e726c2d5423387825cf8af15a64c2026b5099751` |
| 5 | `transfer/pilot-security-hardening` | `e7a35cc1dd52a543e55d98ce3bbd913173c3d85d` |
| 6 | `transfer/customer-onboarding` | `d4da9f43997cedcc3c4828f59919d63224d84c3c` |
| 7 | `transfer/pilot-deployment-ops` | `d3c0da4959cf7f8a2d82171ed6e4564d69f59531` |
| 8 | `transfer/release-validation` | `e04ce68d` |

- `test_rc2_transfer_manifest.py` repinned to these tips (`8ba36956`);
  N6 monotonic-order assertion unchanged and passing.
- S8 vs candidate: **zero diffs** outside docs/coordination/results paths.

**Strict matrix on the merged tree (`8ba36956`):**
- backend 843 pass / 9 skip / 0 fail
- security-agents **828 pass / 24 skip** — includes `test_e2e_replay.py`
  **213/213** (all 59 upstream hook-attribute + symlinked-rootdir tests)
- e2e + manifest + independent-review + N9 probes 10/10 (earlier suites:
  58/1 incl. F1–F10 + N1–N9)
- shared 13/3 · migrations 2/2 head 0019 · transport 36/36
- frontend **405 pass** (#630 tests included) + lint + typecheck + build clean
- `make check` + mypy clean · contracts regen 0-changed

**GitHub CI:** run on `8ba36956` — 22 pass / 1 pending (rl-tests) /
2 skipped (codex-audit no key; live opt-in) / 2 "failures" both judged
non-product:
- `report` exits 2 by design — surfaces the honest NO-GO (no OPENAI_API_KEY)
  after posting + committing the audit (`6ece2484`). Working as intended.
- `e2e` journey spec timed out while snapshot indexing retried
  (`SNAPSHOT_NOT_READY` → retry → ready); **identical spec passes locally
  3/3 in 34.7s on the exact merged tree** — timing flake, not regression.
  Zero backend code changed in this merge.

**Verdict (RC5 final):** candidate SHA **`8ba3695615d0639aa36a2e58667fe414424d4cd8`**
(branch tip `6ece2484` = report commits only). GO FOR INDEPENDENT REVIEW on
the selected tested landing security suite; release merge remains HOLD per
Codex — nothing merged, deployed, or enforcement-enabled.

---

## RC5 second addendum — canonical `ba4ada0d` incorporated (frontend #638)

Codex's exact-SHA check on `b6d5108b` returned **Security GO** (194 pass /
zero fail-skip-error, run 40285189840; F1–F10/N1–N9 + #629 all pass; backend
1317, security 839, frontend 405 unit/46 browser). Canonical main then moved
to `ba4ada0d` (frontend #638 "restore the classic design"), which produced
real merge conflicts — Codex held canonical merge pending reconciliation +
full resulting-SHA CI. Both now satisfied.

**Conflicts resolved** (4 files, first real code conflicts in the cascade):
- `frontend/components/project.tsx` — upstream replaced the grouped rail
  with flat `TABS` + `MORE` dropdown; adopted upstream's structure and added
  `Onboarding` to `MORE` (between Simulate and Setup) — the only integration
  content preserved on top of upstream's layout.
- `frontend/tests/e2e/demo-recovery.spec.ts` — upstream assertions adopted
  (5 tab links, Analytics aria-current).
- `frontend/tests/unit/project-navigation.test.tsx` — upstream assertions
  adopted + `['Onboarding', '/projects/p1/onboarding?repo=r1']` added to the
  More-menu expectation.
- `docs/planning/SESSION_HANDOFF.md` — union-resolved per convention.

**Merge commits:** integration `d8c5ff38`; stack S1 `709c75d7`, S2
`ba7ba2d1`, S3 `93b4e79f`, S4 `931c1f20`, S5 `93e4a81b`, S6 `5ff2254e`,
S7 `d42cb056`, S8 `56a5d1eb` (includes manifest repin). S8 vs candidate:
zero diffs outside docs/coordination/results.

**Verification on merged tree:**
- Zero backend/security-agents/shared production changes — the earlier
  matrix (843/9 backend, 828/24 sec-agents, 213/213 replay, migrations 2/2,
  transport 36) carries over unchanged.
- Frontend: **427 pass / 99 files** (#638's tests included) + lint +
  typecheck + build clean; `project-navigation` 3/3; manifest 2/2.
- **Same-SHA GitHub CI on `36bd05ee`: 24/24 green** — wait-ci +
  strict-regressions + every suite incl. `e2e`; `codex-audit` skipped (no
  key), `live` opt-in skipped, `report` posts the honest CI-only NO-GO.

**Verdict (RC5 final, refreshed):** candidate SHA
**`36bd05ee`** (branch tip `b6829c3f` = report commit, docs only) — GO for
the tested landing security suite; release merge remains HOLD per Codex.
Nothing merged, deployed, or enforcement-enabled.
