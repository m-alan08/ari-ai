"""Codex review residual (inherited, not a #694 regression): six literal forms that
`redact_code` left in the stored/viewer diff under credential-like names.

All values are synthetic placeholders. The sealed diff Verify executed is stored
unredacted and must stay byte-identical.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from backend.artifacts import ArtifactStore
from backend.db import transaction
from backend.redaction import redact_code
from shared.evidence import ArtifactKind

PLACEHOLDER = "not-a-real-value"


def _diff(path: str, *added: str) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(added)} @@\n{body}"
    )


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (f'password = r"{PLACEHOLDER}"', 'password = r"[REDACTED:kv]"'),
        (f"api_key = b'{PLACEHOLDER}'", "api_key = b'[REDACTED:kv]'"),
        (f'client_secret = f"{PLACEHOLDER}"', 'client_secret = f"[REDACTED:kv]"'),
        (f'token = """{PLACEHOLDER}"""', 'token = """[REDACTED:kv]"""'),
        (f"password = '''{PLACEHOLDER}'''", "password = '''[REDACTED:kv]'''"),
        (f'db_password = Rb"{PLACEHOLDER}"', 'db_password = Rb"[REDACTED:kv]"'),
    ],
    ids=["r-prefix", "b-prefix", "f-prefix", "triple-double", "triple-single", "rb-prefix"],
)
def test_prefixed_and_triple_quoted_python_literals_are_redacted(line: str, expected: str) -> None:
    redacted = redact_code(_diff("app/settings.py", line))
    assert f"+{expected}\n" in redacted
    assert PLACEHOLDER not in redacted
    ast.parse(expected)  # the redacted line is still valid Python


@pytest.mark.parametrize("path", ["deploy/values.yaml", "config/app.yml"])
def test_yaml_style_unquoted_values_are_redacted(path: str) -> None:
    diff = _diff(
        path,
        "database:",
        f"  password: {PLACEHOLDER}",
        f"  - api_key: {PLACEHOLDER}  # rotated",
        "  user: app",
        "  token: abc#def",
    )
    redacted = redact_code(diff)
    assert "+  password: [REDACTED:kv]\n" in redacted
    assert "+  - api_key: [REDACTED:kv]  # rotated\n" in redacted
    assert "+  user: app\n" in redacted
    assert "+  token: [REDACTED:kv]\n" in redacted  # "#" without a space is part of the value
    assert PLACEHOLDER not in redacted


@pytest.mark.parametrize("path", [".env", "backend/.env.production", "deploy/staging.env"])
def test_dotenv_style_unquoted_values_are_redacted(path: str) -> None:
    diff = _diff(
        path,
        f"DB_PASSWORD={PLACEHOLDER}",
        f"export GITHUB_TOKEN={PLACEHOLDER} # ci",
        "LOG_LEVEL=info",
    )
    redacted = redact_code(diff)
    assert "+DB_PASSWORD=[REDACTED:kv]\n" in redacted
    assert "+export GITHUB_TOKEN=[REDACTED:kv] # ci\n" in redacted
    assert "+LOG_LEVEL=info\n" in redacted
    assert PLACEHOLDER not in redacted


def test_config_references_and_block_markers_are_left_alone() -> None:
    yaml_diff = _diff("values.yaml", "password: ${DB_PASSWORD}", "secret: |", "token: !vault x")
    env_diff = _diff(".env", "API_KEY=${VAULT_API_KEY}", "SECRET_KEY=")
    assert redact_code(yaml_diff) == yaml_diff
    assert redact_code(env_diff) == env_diff


def test_unquoted_rules_never_touch_python_expressions() -> None:
    """YAML/dotenv-style unquoted values are only recognised in YAML/dotenv files, so
    Python expression boundaries stay intact."""
    lines = (
        "password: str = os.getenv('P')",
        'api_key=os.environ["API_KEY"]',
        "token: Token",
        "secret = load_secret()",
    )
    diff = _diff("app/config.py", *lines)
    assert redact_code(diff) == diff
    bare = "\n".join(lines) + "\n"
    assert redact_code(bare) == bare


def test_format_tracking_follows_each_file_in_a_multi_file_diff() -> None:
    diff = _diff("values.yaml", f"password: {PLACEHOLDER}") + _diff(
        "app/config.py", "password: Password"
    )
    redacted = redact_code(diff)
    assert "+password: [REDACTED:kv]\n" in redacted
    assert "+password: Password\n" in redacted


def test_sealed_diff_keeps_the_exact_verified_bytes(tmp_path: Path) -> None:
    diff = _diff(".env", f"DB_PASSWORD={PLACEHOLDER}") + _diff(
        "app/settings.py", f'password = r"{PLACEHOLDER}"'
    )
    sha = hashlib.sha256(diff.encode()).hexdigest()
    store = ArtifactStore(tmp_path.as_uri())
    with transaction() as conn:
        view = store.put_text(conn, diff, kind=ArtifactKind.diff, project_id=None)
        sealed = store.put_bytes(
            conn,
            diff.encode(),
            kind=ArtifactKind.sealed_diff,
            project_id=None,
            media_type="text/x-diff",
            redacted=False,
        )
    with transaction() as conn:
        _, view_bytes = store.get(conn, view.id)
        _, sealed_bytes = store.get(conn, sealed.id)
    assert PLACEHOLDER.encode() not in view_bytes
    assert sealed_bytes == diff.encode()
    assert sealed.sha256 == sha
