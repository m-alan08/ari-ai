# 15 — Security boundaries

Status: **specified**. These rules are **mandatory** for all code in this
repository. A PR that weakens any of them needs security-owner review and an ADR.
Deep dive: [architecture/sandbox-and-authorization](architecture/sandbox-and-authorization.md).

## 1. Threat model summary

| Threat | Example | Primary control |
|---|---|---|
| Testing an unauthorized target | User or model points Probe at a third-party host | AuthorizedScope + deterministic scope check + sandbox network policy (§3, §4) |
| Sandbox escape / host compromise | Malicious repo code run by tests | Ephemeral containers, no host mounts, non-root, seccomp, limits (§4) |
| Prompt injection from repository content | A comment instructs the agent to exfiltrate secrets or skip checks | Data/instruction separation, tool allowlists, no permission from model output (§5) |
| Secret leakage | API keys in logs, prompts, artifacts | Secret manager, redaction, no secrets in sandboxes (§6) |
| Privilege escalation via model output | Model "approves" its own patch or widens scope | Approvals are API actions by humans only; schemas forbid such fields (§7) |
| Supply chain | Malicious dependency in our own stack | Lockfiles, pinned actions, dependency review (§9) |
| Abuse of the platform | Using it as an attack tool | Sandbox-only targets in MVP, audit log, curated check library (§3, §8) |

## 2. Trust boundaries

1. **Trusted:** our code in `backend/`, worker processes, the database.
2. **Untrusted data:** repository contents, webhook payloads, runtime logs, LLM
   outputs, tool outputs from sandboxes.
3. **Untrusted execution:** anything run inside a sandbox container.

Untrusted data may influence *what is analyzed*, never *what is permitted*.

## 3. Authorization (AuthorizedScope)

- Security testing (Probe, Verify's re-check) requires an `AuthorizedScope` with
  `status = active`, unexpired, whose targets include the target.
- MVP target kind is **only** `sandbox_build`: a container we build from the
  connected repo and run on an isolated network. No external hosts, URLs or IPs.
- Scope approval is an explicit human API action by a project owner. Owners may
  approve their own scopes only when every target is `sandbox_build`
  ([ADR-0016](decisions/0016-owner-sandbox-approval.md)). Other scope kinds and
  remediation/PR approvals retain independent-approver requirements.
- `scope_guard.check(scope_id, target, action)` is a pure deterministic function
  called (a) at `SCOPE_CHECK`, (b) before every network-capable tool invocation,
  (c) by the sandbox driver when creating a network attachment. Denials are
  audited and raise `SCOPE_DENIED`.
- Revoking a scope stops running sandboxes for it within 10 seconds (target).

## 4. Sandbox requirements

| Control | Requirement |
|---|---|
| Isolation | Docker container per step; never execute untrusted code in a worker process |
| Network | `none` by default. `target-only`: an internal Docker network containing only the sandbox and the scoped target container; no egress, no DNS beyond the internal network |
| User | non-root UID; `no-new-privileges`; all capabilities dropped |
| Filesystem | read-only root FS; workspace copy mounted read-only (or copy-in); writable `/out` and tmpfs `/tmp` only; **no** host path mounts, **no** Docker socket |
| Limits | CPU (1 vCPU), memory (`SANDBOX_MAX_MEMORY_MB`, default 2048), PIDs (256), wall clock (`SANDBOX_DEFAULT_TIMEOUT_SECONDS`, default 300), output size (50 MB) |
| Lifetime | Ephemeral; removed after results are collected; reaper deletes orphans older than 2× timeout |
| Secrets | No platform secrets or credentials in env or files; target test credentials are synthetic fixtures |
| Images | Pinned base images by digest; built from `scripts/`-maintained Dockerfiles (later task) |
| Hardening roadmap | gVisor (`runsc`) or Firecracker before handling third-party repos at scale ([ADR-0006](decisions/0006-docker-sandbox-no-network-default.md)) |

## 5. Prompt-injection defenses

- Repository content, logs and tool output go into prompts only inside explicit
  data delimiters with a provenance header; system prompts state that content in
  data sections is never instructions.
- Agents' tool allowlists are fixed per agent and step; the model cannot add tools.
- Tool arguments are schema-validated and policy-checked (e.g. target must equal
  the scoped target ID, never a free-form host).
- Model output can never contain fields that grant permissions (no `approved`,
  `scope`, `skip_*` fields exist in output schemas).
- Suspicious-instruction detection in context (simple pattern rules) is logged as
  a signal, but **is not relied on** as a control.
- Secrets are never placed in prompts, so injection cannot exfiltrate them from
  the model's context.

## 6. Secrets

- Only via environment variables or a secret manager; `.env` is git-ignored;
  `.env.example` contains placeholders.
- Logs and stored LLM transcripts pass through a redactor (known key patterns,
  `*_SECRET`, `*_KEY`, bearer tokens, PEM blocks).
- Verify-tested diffs have a separate sealed artifact containing their exact,
  unredacted bytes. It is delivery-only, is never served through artifact reads,
  and is deleted with the project; delivery verifies its hash and refuses
  high-confidence credentials before sending the diff to GitHub.
- GitHub App tokens are short-lived installation tokens fetched per job.
- gitleaks runs in CI (`INFRA-003`) and is recommended as a pre-commit hook.

## 7. Approvals and model authority

| Action | Who can do it |
|---|---|
| Activate scope | Human approver |
| Start assessment | Human (or monitoring policy within an active scope) |
| Run Probe check | Orchestrator, after scope check |
| Generate patch | Orchestrator, after human remediation approval |
| Open PR | Human click → API with `pr` approval |
| Merge / deploy | **Never the platform** |

## 8. Probe check library

Probe does not improvise attacks. It selects from versioned, code-reviewed,
**non-destructive** check templates (e.g. "request resource owned by fixture user
A while authenticated as fixture user B and compare access decision with the
expectation"). Templates declare category, required scope category, side-effect
class (`read_only` only in MVP), and expected evidence. New templates require
security-owner review. Details: [agents/RED_PROBE](agents/RED_PROBE.md).

## 9. Our own supply chain

Lockfiles committed; dependency updates reviewed; GitHub Actions pinned by SHA;
minimal permissions on workflows; no `pull_request_target` without security
review ([.github/workflows/README](../.github/workflows/README.md)).

## 10. Data handling

- Customer/third-party code and findings never go into this repo, issues, or
  public demos. Demo uses only `examples/` targets we own.
- Retention defaults (MVP): sandbox logs 30 days, transcripts 30 days, findings
  for project lifetime; deletion on project delete.

## 11. Mandatory security-boundary tests

Listed in [16](16_TESTING_AND_VERIFICATION.md) §Security-boundary suite. A
release is blocked if any fails.
