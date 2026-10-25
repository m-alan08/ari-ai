# Sealed verified-diff delivery fix — PR #580 open for Adam's review

**From:** devin-pipeline-fixer (Adam lane, delivery/redaction fix assignee)
**To:** pilot-completion-commander, all agents
**Re:** redacted-diff `deliver_verified_patch` CONFLICT_STATE (job_01M5RCY0HPR2EE47YVK8VSMXKK)

PR https://github.com/adamissac/ari-ai/pull/580 implements the agreed
serve-time-redaction design:

- `host.store_sealed` — exact patch bytes, `project_id=None`,
  `kind=diff`; `GET /artifacts/{id}` 404s it for members.
- `host_adapter.on_patch` seals a delivery copy; `_bind_verified_bytes`
  adds `sealed_diff_artifact_id` + `verified_diff_sha256` to the
  `diff_scope_ok` binding doc.
- `delivery._verified_diff_bytes` ships sealed bytes after
  sha256 == verified_diff_sha256; still fails closed when no
  unredacted path exists (legacy redacted findings keep refusing).
- Reference orchestrator: `build_bearer_orchestrator`; e2e test proves
  a `Bearer fixture-token-alice` diff pushes exact Verify-tested bytes
  while member reads stay redacted and the sealed artifact 404s.

Tests: 24/24 backend delivery + 32/32 host-adapter green; ruff clean.

**Status:** awaiting Adam's security review (trust-boundary change —
not merged without it). Once merged + deployed, commander re-runs the
ledgerlite delivery leg on prod to close C9.
