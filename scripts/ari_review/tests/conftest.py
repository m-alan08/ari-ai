import pytest


@pytest.fixture(autouse=True)
def isolated_signing_key(monkeypatch):
    monkeypatch.setenv("ARI_REVIEW_STATE_KEY", "unit-test-only-state-key-" + "x" * 40)
