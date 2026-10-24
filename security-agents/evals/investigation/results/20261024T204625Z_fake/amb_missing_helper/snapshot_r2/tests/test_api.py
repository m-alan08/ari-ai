"""Baseline tests for report-hub."""

from fastapi.testclient import TestClient

from services.api import create_app

TENANT_A = {"Authorization": "Bearer fixture-token-tenant-a"}
TENANT_B = {"Authorization": "Bearer fixture-token-tenant-b"}


def test_health():
    assert TestClient(create_app()).get("/health").status_code == 200


def test_reports_require_token():
    assert TestClient(create_app()).get("/reports").status_code == 401


def test_list_reports_scoped_to_tenant():
    resp = TestClient(create_app()).get("/reports", headers=TENANT_A)
    assert resp.status_code == 200
    assert {r["tenant"] for r in resp.json()} == {"tenant_a"}


def test_owner_reads_report():
    resp = TestClient(create_app()).get("/reports/rep_a1", headers=TENANT_A)
    assert resp.status_code == 200
    assert resp.json()["title"] == "Weekly revenue"


def test_report_detail_denies_other_tenant():
    resp = TestClient(create_app()).get("/reports/rep_a1", headers=TENANT_B)
    assert resp.status_code == 404
