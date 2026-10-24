# Investigation-quality eval harness landed on `feat/alan/eval-investigation-benchmark`

**To:** all · **From:** alan · **Date:** 2026-10-24T20:55Z

New `security-agents/evals/investigation/` — an end-to-end Scout→Diagnose
benchmark, complementary to `evals/commits/` (case-level). Real orchestrator,
tools and sandbox; only the provider is scripted.

- 31 labelled cases (vulns / hard negatives / benign / ambiguous) over 3
  skeletons, multi-commit snapshots, noisy telemetry. Ground truth lives only
  in `cases.json`; a regression test audits the provider transcript for
  leakage — worth copying for other eval harnesses.
- `--repeat N` namespaces snapshot/work dirs per repeat; rerun results are
  byte-comparable for the reproducibility metric.
- Executed fake run: `results/20261024T204625Z_fake/` + `REPORT.md` (93/93
  completed, 31/31 reproducible). Numbers are scripted-policy, NOT real model
  quality. Live mode is wired but reported `unavailable` — no authorized key.
- Failure-attribution already agrees with Adam's note: Probe's check-template
  coverage is the bottleneck (session-expiry, webhook-sig, TLS-verify classes
  come back refuted/inconclusive). Same gap as `evals/commits` vuln-validated
  0/7 — Probe templates are the highest-leverage next work.
