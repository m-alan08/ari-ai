# Exact-SHA independent release automation

See [setup, limits and trust boundaries](../../docs/release/AUTOMATED_INDEPENDENT_REVIEW.md).
This is CI/control tooling, separate from Ari's customer authorization/enforcement.
It does not modify production code, merge releases or deploy applications.

Run local safety tests from the repository root:

```sh
python -m pytest scripts/ari_review/tests -q
ruff check scripts/ari_review
ruff format --check scripts/ari_review
```

The GitHub tooling workflow additionally checks actionlint and container execution.
Paid capabilities remain disabled until protected-environment setup is complete.
