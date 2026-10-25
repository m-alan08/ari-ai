"""The security agents' sandbox target: the real app, seeded fixture users, bearer mapping."""

import importlib.util
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]

_EXPIRY = re.compile(r"^\{expires_in_days:(\d{1,3})\}$")
_RESOURCE_KIND = re.compile(r"^\{resource:([A-Za-z_][A-Za-z0-9_]*)\}$")


def _render_body(recipe: dict[str, Any], tpl: Any, owner: str) -> Any:
    """Recipe body placeholders — same substitutions the check renderer applies."""
    if isinstance(tpl, dict):
        return {k: _render_body(recipe, v, owner) for k, v in tpl.items()}
    if isinstance(tpl, list):
        return [_render_body(recipe, v, owner) for v in tpl]
    if (m := _EXPIRY.match(str(tpl))) is not None:
        return (datetime.now(UTC) + timedelta(days=int(m.group(1)))).isoformat()
    if (m := _RESOURCE_KIND.match(str(tpl))) is not None:
        return recipe["resources"][m.group(1)][owner]["id"]
    return tpl


def _target():
    spec = importlib.util.spec_from_file_location(
        "sandbox_target", ROOT / "deploy" / "sandbox_target.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_recipe_route_allows_the_owner_and_denies_the_other_user(monkeypatch):
    monkeypatch.setenv("SANDBOX_TARGET_DATABASE_URL", os.environ["DATABASE_URL"])
    recipe = json.loads((ROOT / "build_recipe.json").read_text())
    client = TestClient(_target().create_app())
    ident = {k: {"Authorization": v["auth_header"]} for k, v in recipe["identities"].items()}
    for route in recipe["routes"]:
        rid = recipe["resources"][route["resource"]]["alice"]["id"]
        path = route["path_template"].split("{")[0] + rid + route["path_template"].split("}")[1]
        method = route.get("method", "GET")
        body = _render_body(recipe, route.get("body_template"), "alice")
        assert client.request(method, path, headers=ident["alice"], json=body).status_code in (
            200,
            201,
        ), route["id"]
        assert client.request(method, path, headers=ident["bob"], json=body).status_code in (
            403,
            404,
        ), route["id"]
        assert client.request(method, path, json=body).status_code == 401, route["id"]
