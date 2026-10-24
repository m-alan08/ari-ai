"""Baseline tests for shop-lite (kept green across eval edits that do not change behavior)."""

from fastapi.testclient import TestClient

from shop.app import create_app

ALICE = {"Authorization": "Bearer fixture-token-alice"}
BOB = {"Authorization": "Bearer fixture-token-bob"}


def test_health():
    assert TestClient(create_app()).get("/health").status_code == 200


def test_me_requires_token():
    assert TestClient(create_app()).get("/me").status_code == 401


def test_me_returns_alice():
    resp = TestClient(create_app()).get("/me", headers=ALICE)
    assert resp.status_code == 200
    assert resp.json()["id"] == "cus_alice"


def test_customer_detail_denies_other_customer():
    resp = TestClient(create_app()).get("/customers/cus_alice", headers=BOB)
    assert resp.status_code == 404


def test_owner_reads_own_order():
    resp = TestClient(create_app()).get("/orders/ord_a1", headers=ALICE)
    assert resp.status_code == 200
    assert resp.json()["sku"] == "sku-chair"


def test_invoice_detail_denies_other_customer():
    resp = TestClient(create_app()).get("/invoices/inv_a1", headers=BOB)
    assert resp.status_code == 404


def test_search_returns_products():
    resp = TestClient(create_app()).get("/search", params={"q": "lamp"}, headers=ALICE)
    assert resp.status_code == 200
    assert resp.json()[0]["sku"] == "sku-lamp"


def test_invoice_payment_over_balance_rejected():
    client = TestClient(create_app())
    resp = client.post(
        "/invoices/inv_a1/payments", json={"amount": "80.01"}, headers=ALICE
    )
    assert resp.status_code == 422
