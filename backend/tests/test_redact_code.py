import ast
import hashlib
from pathlib import Path

from backend.artifacts import ArtifactStore
from backend.db import transaction
from backend.redaction import redact_code, redact_text
from shared.evidence import ArtifactKind


def test_code_redaction_preserves_ledgerlite_lines_and_diff() -> None:
    declaration = "    tokens: dict[str, str] = field(default_factory=dict)"
    assignment = "    store.tokens = {"
    diff = (
        "--- a/ledgerlite/store.py\n"
        "+++ b/ledgerlite/store.py\n"
        "@@ -1,2 +1,2 @@\n"
        f"-{declaration}\n"
        f"+{assignment}\n"
    )

    assert redact_code(declaration) == declaration
    assert redact_code(assignment) == assignment
    assert redact_code(diff) == diff
    for source in ("api_token = DEFAULT_TOKEN", "password = load_secret()"):
        assert redact_code(source) == source


def test_diff_artifact_preserves_original_bytes_and_sha(tmp_path: Path) -> None:
    diff = (
        "--- a/ledgerlite/store.py\n"
        "+++ b/ledgerlite/store.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-    tokens: dict[str, str] = field(default_factory=dict)\n"
        "+    store.tokens = {\n"
    )
    expected_sha = hashlib.sha256(diff.encode()).hexdigest()
    store = ArtifactStore(tmp_path.as_uri())

    with transaction() as conn:
        ref = store.put_text(conn, diff, kind=ArtifactKind.diff, project_id=None)
    with transaction() as conn:
        _, stored_bytes = store.get(conn, ref.id)

    assert ref.sha256 == expected_sha
    assert hashlib.sha256(stored_bytes).hexdigest() == expected_sha
    assert stored_bytes == diff.encode()


def test_code_redaction_scrubs_quoted_values_and_high_confidence_secrets() -> None:
    github_token = "ghp_" + "a" * 20
    aws_key = "AKIA" + "A" * 16
    stripe_key = "sk_live_" + "x" * 24
    jwt = "eyJhbGci.eyJzdWI.sig"
    dsn = "postgresql://" + "u:p@h/db"
    diff = (
        "--- a/example.py\n"
        "+++ b/example.py\n"
        "@@ -1,6 +1,6 @@\n"
        '+password = "hunter2"\n'
        "+client_secret = 'quoted value'\n"
        f'+github_value = "{github_token}"\n'
        f'+aws_value = "{aws_key}"\n'
        f'+stripe_value = "{stripe_key}"\n'
        f'+jwt_value = "{jwt}"\n'
        f'+dsn_value = "{dsn}"\n'
    )

    redacted = redact_code(diff)

    assert '+password = "[REDACTED:kv]"' in redacted
    assert "+client_secret = '[REDACTED:kv]'" in redacted
    assert "[REDACTED:github_token]" in redacted
    assert "[REDACTED:aws_key]" in redacted
    assert "[REDACTED:stripe_key]" in redacted
    assert "[REDACTED:jwt]" in redacted
    assert "[REDACTED:dsn_credentials]" in redacted
    for secret in (github_token, aws_key, stripe_key, jwt, dsn, "hunter2"):
        assert secret not in redacted


def test_code_redaction_keeps_python_snippet_parseable() -> None:
    github_token = "ghp_" + "b" * 20
    snippet = f'password = "hunter2"\ngithub_value = "{github_token}"\n'

    redacted = redact_code(snippet)

    ast.parse(redacted)
    assert 'password = "[REDACTED:kv]"' in redacted
    assert "[REDACTED:github_token]" in redacted


def test_code_redaction_only_scrubs_same_line_quoted_values() -> None:
    source = 'password =\n    "hunter2"\n'

    assert redact_code(source) == source


def test_log_redaction_keeps_generic_key_value_behavior() -> None:
    assert redact_text("token = identifier") == "[REDACTED:kv]"
    assert redact_text('password = "hunter2"') == "[REDACTED:kv]"
