"""Regression tests for redact_text gaps found by post-merge probing (pilot gate)."""

from backend.redaction import redact_text


def test_psycopg_dsn() -> None:
    out = redact_text("connect postgresql+psycopg://svc:pw9x@db/app")
    assert "pw9x" not in out


def test_access_token_kv() -> None:
    out = redact_text("access_token=" + "ya29." + "b" * 12)
    assert "ya29.bbbb" not in out


def test_db_password_kv() -> None:
    out = redact_text("DB_PASSWORD=hunter2")
    assert "hunter2" not in out


def test_client_secret_kv() -> None:
    out = redact_text("client_secret: " + "GOCSPX-" + "d" * 11)
    assert "GOCSPX-dddd" not in out


def test_stripe_live_key() -> None:
    out = redact_text("key " + "sk_live_" + "x" * 32 + " tail")
    assert "sk_live_" not in out


def test_quoted_kv_with_space() -> None:
    out = redact_text("err: password='quoted Pw1' done")
    assert "quoted Pw1" not in out


def test_bearer_token_short() -> None:
    out = redact_text("Authorization: Bearer eyJabc.def.ghi")
    assert "eyJabc" not in out


def test_double_quoted_kv() -> None:
    out = redact_text('refresh_token: "r3fr3sh"')
    assert "r3fr3sh" not in out
