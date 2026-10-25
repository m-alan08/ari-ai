"""Regression tests for leak classes found by post-merge probing (pilot gate)."""

from code_intelligence.privacy import redact


def test_slack_token_in_assignment() -> None:
    out = redact('slack_token="xoxb-" + "1" * 12 + "-" + "a" * 12')
    assert "xoxb-" not in out
    assert "[REDACTED]" in out


def test_generic_token_key() -> None:
    out = redact('token="tok-" + "c" * 9')
    assert "tok-ccccccccc" not in out


def test_stripe_live_key() -> None:
    out = redact('STRIPE_KEY = "' + "sk_live_" + "x" * 32 + '"')
    assert "sk_live_" not in out


def test_postgres_dsn_credentials() -> None:
    out = redact("DATABASE_URL=postgresql://svc:hunter2pass@db.internal:5432/app")
    assert "hunter2pass" not in out
    assert "postgresql://" in out  # scheme survives


def test_psycopg_dsn_credentials() -> None:
    out = redact("url = postgresql+psycopg://svc:secretpass@db/app")
    assert "secretpass" not in out


def test_jwt() -> None:
    jwt = (
        "eyJ"
        + "hbGciOiJIUzI1NiJ9"
        + "."
        + "eyJ"
        + "zdWIiOiIxMjM0NTY3ODkwIn0"
        + "."
        + "SflKxw"
        + "RJSMeKKF2QT4fwpMeJf36POk6yJV"
        + "_adQssw5c"
    )
    out = redact(f"h = {jwt}")
    assert "SflKxw" not in out


def test_google_api_key() -> None:
    out = redact('key = "' + "AIza" + "q" * 35 + '"')
    assert "AIzaqqq" not in out


def test_paren_concat_multiline() -> None:
    src = 'password = (\n    "sup"\n    "ersecret"\n)'
    out = redact(src)
    assert "ersecret" not in out
    assert out.count("\n") == src.count("\n")  # line-preserving


def test_comma_continued_literals() -> None:
    out = redact('api_key = "first","second"')
    assert "first" not in out
    assert "second" not in out


def test_environ_default_value() -> None:
    out = redact('pw = os.environ.get("X_PASSWORD","fallback-pw-9")')
    assert "fallback-pw-9" not in out
    assert "X_PASSWORD" in out


def test_midline_bare_token_without_digits() -> None:
    out = redact("auth = dict(password=hunter, other=1)")
    assert "hunter" not in out


def test_midline_reference_still_redacted() -> None:
    # bare mid-line secrets are redacted conservatively (value or identifier)
    out = redact("opts = (api_key=somekey, retries=2)")
    assert "somekey" not in out


def test_normal_line_untouched() -> None:
    src = "def read_payment(order_id):\n    return db.get(order_id)\n"
    assert redact(src) == src
