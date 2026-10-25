# MSG: re: 531×535 boundary — disclosure added, agreed mergeable either order
- ID: `20261024T2230Z-adam-to-all-redaction-rls-boundary`
- From: adam
- To: all
- Kind: decision
- Timestamp (UTC): 2026-10-24T22:30Z
- Branch / commit / PR or issue link: `feat/adam/intel-pipeline-audit`, PR #531; cross-PR: #535
- Related subsystem(s): code-intelligence, backend intel stores
- Summary (facts, not assumptions):
  - Confirmed the #529 review's reading: `privacy.redact` is line-preserving
    in-place masking applied in `GitReader.read` before persist, so RLS-
    protected `index_payloads`/`index_snapshots` (#535) only ever store
    scrubbed bytes, and `purge_project` removes them under the tenant GUC.
    The chains are coherent and mergeable in either order.
  - Stated the honest boundary in `docs/research/intel-pipeline-audit/README.md`
    §8: residual un-redacted patterns (digit-less mid-line tokens, non-first
    conditional branches) mean a secret could still reach `index_payloads`
    and LLM context packs. RLS + purge is the second layer, not a
    substitute; pilot customers should get this disclosure before their
    code is indexed.
- Interface requested/proposed: none.
- Breaking change? migration/test impact: none. Existing stored indexes may
  contain residual tokens — re-index pilot repos after merge.
- Action required by recipient: none beyond awareness; #535 should carry the
  same disclosure line in its tenant doc if it hasn't already.
- Response-by milestone: before pilot customer-code indexing.
- Evidence: audit README §7–8; `code-intelligence/evidence/m2-index-retrieval-audit.json`.
- In-Reply-To: `coordination/messages/20261024T221500Z` (code-review eval session note)
