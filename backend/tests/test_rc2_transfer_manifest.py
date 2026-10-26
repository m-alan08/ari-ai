"""Read-only checks of exact upstream-targeted transfer Git objects."""

import ast
import subprocess


def _literal(sha, path, variable):
    source = subprocess.check_output(["git", "show", sha + ":" + path], text=True)
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == variable for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(variable)


def test_proposed_transfer_migrations_do_not_reuse_revision_ids():
    reliability = _literal(
        "93b4e79f7564d61b43c1a0a89e3edc80e45ec6ac",
        "backend/backend/migrations/versions/0018_execution_reliability.py",
        "revision",
    )
    disconnect = _literal(
        "93e4a81b43045e61bedcf652550a458185be3d97",
        "backend/backend/migrations/versions/0019_repository_disconnect.py",
        "revision",
    )
    assert reliability != disconnect, "both transfer branches still declare revision " + reliability


def test_declared_transfer_order_does_not_downgrade_schema():
    heads = [
        "ba7ba2d1217a842239520ed9e1791c94dfade3fc",
        "931c1f200e2f2f54395684b5818bf5fc2b2d3572",
        "93e4a81b43045e61bedcf652550a458185be3d97",
        "5ff2254e5bb145a4553565620ce78ce5c65389b5",
    ]
    versions = [_literal(sha, "shared/shared/__init__.py", "SCHEMA_VERSION") for sha in heads]
    assert all(isinstance(v, str) for v in versions), versions
    numeric = [tuple(map(int, v.split("."))) for v in versions]
    assert numeric == sorted(numeric), versions
