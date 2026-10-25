# Labelled commit eval

Measures the live pipeline (code intelligence → reviewer → seven agents) on labelled commits
against this repository, and how long each takes. It is the yardstick for every prompt, tool,
model or specialist change: keep a change only if it scores at least as well here and every
run stays under 180 s.

- `cases.json`: 45 one-commit cases. `idor` (8): an access-control defect on a route in
  `build_recipe.json`, which the ownership check template can prove, so the full chain should
  validate, fix and verify it. `vuln` (24): real weaknesses across 20 CWEs (timing compare,
  missing signature check, SQL injection, path traversal, shell injection, OAuth state,
  argument injection, session expiry, CSRF exemption, integrity digest, TOCTOU, over-broad
  token permissions, webhook replay, insecure cookie, missing auth checks, TLS verify-off,
  unbounded queries, weak input validation), scored on detection. `benign` (13): no weakness;
  a validated finding is a false positive. Commit messages are ordinary and never name the
  defect.
- `run.py`: pushes each case as its own branch from `origin/main` (batches, default 4 at once),
  waits for the assessments, scores them, closes the fix PRs the platform opens and deletes the
  branches. The eval branches never reach `main`.
- `score.py`: runs inside the platform (needs `DATABASE_URL`), reached through `ARI_EVAL_SSH`.

```bash
ARI_EVAL_SSH="ssh -i <key> <service>@ssh.railway.com" uv run python security-agents/evals/commits/run.py --batch 4
```

## Results

| Run | IDOR validated / fixed | Other flagged / validated | Benign false positives | Runs > 180 s | Max / median s | USD |
|---|---|---|---|---|---|---|
| baseline 2026-10-24 (4 at once) | 8/8 / 8/8 | 6/7 / 0/7 | 0/8 | 5/23 | 259 / 74 | 0.80 |

The five slow runs were full IDOR chains run four at a time; the slowest spent 120 s in Patch,
whose test runs after an edit ran one after another (fixed in #491). The other weakness classes
are flagged but cannot be proven yet: only the ownership and bounds check templates exist.
