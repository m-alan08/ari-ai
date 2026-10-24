"""report-hub API service (mono-repo layout: services/, shared/).

Intended behavior:

- A tenant can only read reports owned by that tenant.
- List endpoints clamp page size via shared.util.clamp_limit.
- Report generation only runs allow-listed commands.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status

from shared.util import clamp_limit, require_tenant
from services.jobs import run_report_command
from services.store import Report, Store, seeded_store


def create_app(store: Store | None = None) -> FastAPI:
    app = FastAPI(title="report-hub", version="0.1.0")
    app.state.store = store or seeded_store()

    def get_store() -> Store:
        return app.state.store

    def current_principal(
        authorization: Annotated[str | None, Header()] = None,
        store: Store = Depends(get_store),
    ) -> str:
        """Resolve the bearer token to a tenant id; 401 if missing or unknown."""
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        tenant = store.tokens.get(authorization.removeprefix("Bearer "))
        if tenant is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
        return tenant

    def report_out(r: Report) -> dict[str, str]:
        return {"id": r.id, "tenant": r.tenant, "title": r.title, "status": r.status}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/reports")
    def list_reports(
        limit: int = 50,
        tenant: str = Depends(current_principal),
        store: Store = Depends(get_store),
    ) -> list[dict[str, str]]:
        """List the current tenant's reports, page size clamped."""
        n = limit
        rows = [r for r in store.reports.values() if r.tenant == tenant]
        return [report_out(r) for r in rows[:n]]

    @app.get("/reports/{report_id}")
    def get_report(
        report_id: str,
        tenant: str = Depends(current_principal),
        store: Store = Depends(get_store),
    ) -> dict[str, str]:
        """Return one report by id."""
        r = store.reports.get(report_id)
        if r is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        require_tenant(r.tenant, tenant)
        return report_out(r)

    @app.post("/reports/{report_id}/generate", status_code=status.HTTP_202_ACCEPTED)
    def generate_report(
        report_id: str,
        tenant: str = Depends(current_principal),
        store: Store = Depends(get_store),
    ) -> dict[str, str]:
        """Kick off report generation via the allow-listed job command."""
        r = store.reports.get(report_id)
        if r is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        require_tenant(r.tenant, tenant)
        run_report_command("render", [report_id])
        return {"status": "queued"}

    return app


app = create_app()
