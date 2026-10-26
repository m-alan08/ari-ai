# Control-plane developer commands (uv + embedded or Compose PostgreSQL).
.PHONY: install dev-db migrate api worker dev check test contracts smoke stop-db

install:
	uv sync

dev-db:
	@uv run python -m backend.devdb

migrate:
	DATABASE_URL=$${DATABASE_URL:-$$(uv run python -m backend.devdb)} uv run hackatl-migrate

api:
	DATABASE_URL=$${DATABASE_URL:-$$(uv run python -m backend.devdb)} APP_ENV=development AUTH_DEV_LOGIN_ENABLED=true \
	  uv run uvicorn backend.main:app --reload --port 8000

worker:
	DATABASE_URL=$${DATABASE_URL:-$$(uv run python -m backend.devdb)} uv run hackatl-worker

# API + worker against the embedded PostgreSQL (no Docker needed).
dev: migrate
	$(MAKE) -j2 api worker

contracts:
	uv run python -m shared.export
	uv run python -m shared.examples

check:
	uv run ruff check shared monitoring backend standards
	uv run ruff format --check shared monitoring backend standards
	uv run python -m shared.export --check
	uv run python -m shared.examples --check

test:
	uv run pytest -q

# Tier 3 live provider smoke: requires ANTHROPIC_API_KEY and explicit opt-in. Costs money.
smoke-live:
	RUN_LIVE_LLM_EVAL=1 uv run pytest -q -m live_llm -s

# Live Defense owned-staging e2e tiers (backend/tests/live_defense_e2e).
# Fast deterministic subset for every PR:
e2e-ld-smoke:
	uv run pytest -q -m live_defense_smoke backend/tests/live_defense_e2e

# Full deterministic integration suite (scenarios A-H, FakeLLM):
e2e-ld:
	uv run pytest -q -m live_defense_e2e backend/tests/live_defense_e2e

# Failure-injection suite only (provider/worker/OFF-mode faults):
e2e-ld-fault:
	uv run pytest -q -m live_defense_fault backend/tests/live_defense_e2e

# OPT-IN live-provider check. Requires RUN_LIVE_LLM_EVAL=1 and a real
# HACKATL_LIVE_ANTHROPIC_API_KEY; calls the real Anthropic API and costs money.
e2e-ld-live:
	RUN_LIVE_LLM_EVAL=1 uv run pytest -q -m "live_llm and live_defense_e2e" backend/tests/live_defense_e2e -s

smoke: check test

stop-db:
	uv run python -m backend.devdb --stop

# Offline verification of pinned standards data (no downloads at runtime).
standards-data:
	uv run python -m standards.catalog --check
