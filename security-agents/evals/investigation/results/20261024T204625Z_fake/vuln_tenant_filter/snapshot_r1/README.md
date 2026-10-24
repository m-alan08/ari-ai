# report-hub (mono layout)

Two services sharing `shared/`:

- `services/api.py` — report read/generate API. Reports are tenant-scoped;
  every report route resolves the bearer token to a tenant and enforces
  `shared.util.require_tenant`.
- `services/jobs.py` — allow-listed report commands and the sqlite export used
  by nightly reconciliation.

## Behavior

- A tenant can only read reports it owns.
- List endpoints clamp page size (`shared.util.clamp_limit`, max 500).
- Report commands run only from `ALLOWED_COMMANDS` as argv lists.
- `export_rows` uses parameterized queries only.
