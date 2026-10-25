# MSG: code-intelligence redaction guarantees after the pipeline audit
- ID: `20261024T2215Z-adam-to-all-intel-redaction-guarantees`
- From: adam
- To: all (privacy/security lane: srihith, sarosh)
- Kind: handoff
- Timestamp (UTC): 2026-10-24T22:15Z
- Branch / commit / PR or issue link: `feat/adam/intel-pipeline-audit`, PR #531 (supersedes #441)
- Related subsystem(s): code-intelligence, backend intel stores
- Summary (facts, not assumptions):
  - Source is redacted in `GitReader.read` BEFORE parse and before any storage,
    so SQLite/Postgres index payloads and context packs only ever contain
    redacted content. Stored intelligence and agent context never see raw
    source — the git blob on disk is the only unredacted copy.
  - `_ASSIGN` redacts: quoted string values, bare tokens at EOL/comment, and
    (new) bare tokens containing a digit at mid-line `,`/`;` or a closing
    bracket `)]}`. Corpus-verified: 0 files broken, 0 line shifts, 0
    collateral across all 584 tracked .py.
  - Known residuals (documented, not hidden): mid-line bare tokens with no
    digit (`f(password=hunter)`), and non-first branches of inline
    conditionals (`password="x" if c else "y"`). Regex cannot see call or
    conditional boundaries; an AST-aware redaction pass would close these if
    we ever need it.
  - Non-literal route decorators (`app.get(CONST)`) are now flagged as
    IndexIssue findings instead of silently dropped.
- Interface requested/proposed: none — no shared contract changes.
- Breaking change? migration/test impact: none; stored indexes built before
  this change may contain the (previously unredacted) residual tokens — a
  re-index of pilot repos after merge is the cheapest cleanup.
- Action required by recipient: privacy/security owners — sanity-check the
  residual list in `docs/research/intel-pipeline-audit/README.md` §8 and
  flag any case where a pilot repo is likely to hit a still-unredacted
  pattern before onboarding it.
- Response-by milestone: before next pilot repo onboarding.
- Evidence: `PYTHONPATH=code-intelligence:shared pytest code-intelligence/tests` 60 passed 1 skipped;
  corpus redaction diff script output in session; `code-intelligence/evidence/m2-index-retrieval-audit.json`.
- In-Reply-To: —
