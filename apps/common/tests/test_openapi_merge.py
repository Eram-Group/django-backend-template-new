"""merge_auth_openapi: one spec covering the API and the auth endpoints.

Also the pin for allauth.headless.spec.internal.schema (internals, no
deprecation policy - same reasoning as the sessionkit pin in
config/api/auth.py).
"""

import json
from pathlib import Path
from typing import Any
from typing import cast

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.common.management.commands.merge_auth_openapi import merge_auth_spec
from config.api.v1 import api

SERVER_URL = "https://api.example.com"


def _ninja_spec() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(json.dumps(api.get_openapi_schema())))


def test_allauth_spec_internals_pin() -> None:
    from allauth.headless.spec.internal.schema import get_schema

    spec = get_schema()
    assert spec["paths"], "allauth spec generator returned no paths"


def test_merge_adds_auth_paths_with_operation_ids_and_tags() -> None:
    merged = merge_auth_spec(_ninja_spec(), server_url=SERVER_URL)

    auth_paths = [path for path in merged["paths"] if "auth" in path]
    assert auth_paths, list(merged["paths"])
    for path, methods in merged["paths"].items():
        for method, operation in methods.items():
            if not isinstance(operation, dict) or method.startswith("x-"):
                continue
            assert operation.get("operationId"), (path, method)
    sample = merged["paths"][auth_paths[0]]
    sample_op = next(op for op in sample.values() if isinstance(op, dict))
    assert all(tag.startswith("Authentication / ") for tag in sample_op["tags"])


def test_merge_attaches_accept_language_everywhere_and_servers() -> None:
    merged = merge_auth_spec(_ninja_spec(), server_url=SERVER_URL)

    parameter = merged["components"]["parameters"]["AcceptLanguage"]
    assert parameter["schema"]["enum"] == [code for code, _ in settings.LANGUAGES]
    assert parameter["schema"]["default"] == settings.LANGUAGE_CODE
    me_path = next(path for path in merged["paths"] if path.endswith("/users/me"))
    me_get = merged["paths"][me_path]["get"]
    refs = [p.get("$ref") for p in me_get["parameters"] if isinstance(p, dict)]
    assert refs.count("#/components/parameters/AcceptLanguage") == 1
    assert merged["servers"] == [{"url": SERVER_URL}]


def test_merging_twice_is_a_loud_error_not_a_silent_no_op() -> None:
    merged = merge_auth_spec(_ninja_spec(), server_url=SERVER_URL)
    with pytest.raises(CommandError, match="collides"):
        merge_auth_spec(merged, server_url=SERVER_URL)


def test_command_rewrites_the_input_in_place(tmp_path: Path) -> None:
    input_path = tmp_path / "openapi.json"
    input_path.write_text(json.dumps(_ninja_spec()))

    call_command("merge_auth_openapi", input=str(input_path), server_url=SERVER_URL)

    merged = json.loads(input_path.read_text())
    assert any("auth" in path for path in merged["paths"])
    assert merged["servers"] == [{"url": SERVER_URL}]
