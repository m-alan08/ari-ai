"""Baseline tests for files-lite."""

from fastapi.testclient import TestClient

from app import create_app

ALICE = {"Authorization": "Bearer fixture-token-alice"}
BOB = {"Authorization": "Bearer fixture-token-bob"}


def test_health():
    assert TestClient(create_app()).get("/health").status_code == 200


def test_document_requires_token():
    assert TestClient(create_app()).get("/documents/doc_a1").status_code == 401


def test_document_denies_other_user():
    resp = TestClient(create_app()).get("/documents/doc_a1", headers=BOB)
    assert resp.status_code == 404


def test_owner_reads_document():
    resp = TestClient(create_app()).get("/documents/doc_a1", headers=ALICE)
    assert resp.status_code == 200
    assert resp.json()["name"] == "quarterly-report.txt"


def test_share_requires_token_param():
    assert TestClient(create_app()).get("/share/doc_a1").status_code == 404


def test_share_with_token():
    resp = TestClient(create_app()).get("/share/doc_a1", params={"token": "shr_tok_a1"})
    assert resp.status_code == 200


def test_fetch_rejects_unknown_host():
    resp = TestClient(create_app()).post(
        "/fetch", json={"url": "https://evil.invalid/x"}, headers=ALICE
    )
    assert resp.status_code == 400
