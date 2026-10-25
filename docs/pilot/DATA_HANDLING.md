# Data-handling disclosure (draft for pilot customers)

Status: **draft for legal/founder review**, produced jointly by the pilot
launch owner and the security/privacy workstream. Every statement below was
verified in code on `main` (`d0131167`, 2026-10-24). Do not publish until the
privacy session's threat model and retention runbook land — cross-link them
here when they do.

## What Ari stores — plainly

Ari stores your source code, not just metadata:

1. **Bare git clones** of each connected repository on the worker filesystem
   (`$SECURITY_WORKDIR/repos/{repo_id}.git`, the Railway service volume).
   Fetched at the exact commit with a short-lived installation token passed
   via an HTTP env header — never argv, git config, or logs.
2. **Parsed/indexed file content** in PostgreSQL (`index_payloads`, up to
   64 MiB per key) keyed by project, repo, commit SHA and index version;
   immutable and sha256-verified on read.
3. **Artifacts**: diffs, logs, agent transcripts (including prompts that
   contain your code), webhook payloads and evaluation summaries in a
   content-addressed store (`file:///data/artifacts`). Identical bytes across
   projects share one storage object; per-artifact access still requires
   project membership. Transcripts are stored after recursive secret
   redaction.
4. **Control-plane records**: findings, approvals, scopes, assessments,
   incidents, monitoring events, audit events, sessions.

## Retention and deletion — the honest version

- **Nothing expires automatically.** There are no retention jobs and no
  self-serve delete. Sessions time out; everything else persists until an
  operator removes it.
- **Deletion is operator-invoked, not self-serve.** Your request triggers
  the operator-run `purge_project` procedure over database rows, artifact
  files and bare clones (see
  [TENANT_DATA_PROTECTION](https://github.com/adamissac/ari-ai/pull/535) (lands with PR #535)). Target:
  within **30 days** of a verified request. Content-addressed artifact
  sharing means per-customer artifact deletion currently needs manual care —
  we disclose this rather than pretend it is atomic.
- **Backups/copies**: Railway/Postgres backups are provider-managed; deleted
  data may persist in backups per the provider's rotation.

## External AI processing (Anthropic)

- Every model call goes through our LLM gateway; **your repository code
  context is sent to Anthropic's API** — that is how the analysis works.
- Before any provider call, `redact()` strips recognized credential patterns
  (Anthropic/GitHub/AWS/Slack tokens, private keys, bearer tokens) and
  credential-keyed values from prompts and tool results. One known residual:
  assistant-role turns are re-sent unredacted because provider signatures
  bind to exact bytes — inbound redaction mitigates but does not eliminate
  this.
- **Provider retention**: Anthropic's standard API terms apply (including
  their abuse-monitoring retention). We do **not** claim zero retention on
  the provider side and have no evidence of a custom agreement — ask us if
  that changes.

## Who can access your data

- **Application-level tenant isolation**: every project-scoped route resolves
  org membership; non-members get 404 (no existence leak). Same enforcement
  on storage accessors and the intelligence memory layer.
- **Membership isolation + row-level security on source-content tables** —
  every project-scoped route resolves org membership in application code
  (the primary boundary), and Postgres RLS additionally guards the tables
  holding your source content. See RISK_REGISTER R-02 and
  [TENANT_DATA_PROTECTION](https://github.com/adamissac/ari-ai/pull/535) (lands with PR #535).
- **Operator access**: the Ari team can access the Railway volume, database
  and logs for operation and support. Access is hash-chain-audited
  (per-org `audit_events`, owner-readable). There is no customer-visible
  operator-access log today.
- **Sub-processors**: GitHub (auth + repo access), Anthropic (model
  processing), Railway (compute + storage), Vercel (frontend + execution
  sandboxes). Data is sent to each only for its function.

## Security boundaries (verified)

- Untrusted code executes only in ephemeral microVM sandboxes (prod: Vercel
  Sandbox; CI: hardened Docker, `--network none`, cap-drop ALL, read-only,
  tmpfs). Never on the API host. The local-process driver refuses to run
  outside dev/test.
- Security checks run only against targets inside an Approved
  `AuthorizedScope`; the scope check is deterministic code evaluated before
  every network-capable tool call.
- No model output can widen permissions, approve itself, or merge/deploy
  code. Approvals and remediation gates are deterministic records.
- Ingest tokens (`hkt_ing_`) are per-project, SHA-256 stored, revocable;
  secret-looking payloads are quarantined and not stored.
- GitHub access uses installation tokens only — least-privilege per call,
  write tokens pinned to a single repo, 1-hour GitHub TTL, memory-only cache.

## What we do NOT claim

No SOC 2 / certification. No zero-retention. No dedicated single-tenant
infrastructure. No autonomous defense acting on your systems.

## Your controls (summary — detail in CUSTOMER_ONBOARDING.md)

Uninstall the App (immediate access cutoff) · disable automatic runs or
monitoring · cancel a run · revoke the scope · revoke ingest tokens ·
request deletion via support.
