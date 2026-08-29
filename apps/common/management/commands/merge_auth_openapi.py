"""Merge allauth's headless auth spec into an exported ninja OpenAPI file.

The Apidog sync uploads ONE spec - without this merge the entire auth API
never reaches the published docs. Fixes applied while merging, in place:

- allauth's spec ships without operationIds (breaks importers/generators):
  deterministic ids derive from method+path.
- auth operations get "Authentication / <tag>" tags so they group cleanly.
- a global Accept-Language header parameter (settings.LANGUAGES) is attached
  to every operation - localized responses are a headline feature and must
  be discoverable in the docs.
- servers[] is the deployed base URL.

Pinned allauth internals: allauth.headless.spec.internal.schema.get_schema
(covered by tests, same pin style as config/api/auth.py's sessionkit).
"""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from allauth.headless.spec.internal.schema import get_schema
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import CommandParser

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
_ACCEPT_LANGUAGE_REF = "#/components/parameters/AcceptLanguage"


def _accept_language_parameter() -> dict[str, Any]:
    return {
        "name": "Accept-Language",
        "in": "header",
        "required": False,
        "description": f"Response language ({settings.LANGUAGE_CODE} without it).",
        "schema": {
            "type": "string",
            "enum": [code for code, _label in settings.LANGUAGES],
            "default": settings.LANGUAGE_CODE,
        },
    }


def _operation_id(method: str, path: str) -> str:
    digest = hashlib.md5(  # noqa: S324 - non-cryptographic, stable id
        f"{method}:{path}".encode()
    ).hexdigest()
    return f"auth_{digest[:12]}"


def _operations(paths: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """(path, method, operation) for every real operation - path items also
    carry non-operation keys (parameters, x-* extensions)."""
    for path, methods in paths.items():
        for method, operation in methods.items():
            if method in _HTTP_METHODS and isinstance(operation, dict):
                yield path, method, operation


def merge_auth_spec(spec: dict[str, Any], *, server_url: str) -> dict[str, Any]:
    auth = get_schema()

    for path in auth["paths"]:
        if path in spec["paths"]:
            msg = f"auth path collides with the API spec: {path}"
            raise CommandError(msg)
    for path, method, operation in _operations(auth["paths"]):
        operation["operationId"] = _operation_id(method, path)
        operation["tags"] = [
            f"Authentication / {tag}"
            for tag in operation.get("tags", ["Authentication"])
        ]
    spec["paths"].update(auth["paths"])

    components = spec.setdefault("components", {})
    for section, entries in auth.get("components", {}).items():
        target = components.setdefault(section, {})
        for name, entry in entries.items():
            if name in target and target[name] != entry:
                msg = f"auth component collides with the API spec: {section}.{name}"
                raise CommandError(msg)
            target[name] = entry

    parameters = components.setdefault("parameters", {})
    if "AcceptLanguage" in parameters:
        msg = "the API spec already defines components.parameters.AcceptLanguage"
        raise CommandError(msg)
    parameters["AcceptLanguage"] = _accept_language_parameter()
    for _path, _method, operation in _operations(spec["paths"]):
        operation.setdefault("parameters", []).append({"$ref": _ACCEPT_LANGUAGE_REF})

    spec["servers"] = [{"url": server_url}]
    return spec


class Command(BaseCommand):
    help = "Merge the allauth headless auth spec into an exported openapi.json."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--input", required=True, help="ninja export, rewritten")
        parser.add_argument(
            "--server-url", required=True, help="servers[] entry: deployed base URL"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["input"])
        spec = json.loads(path.read_text())
        merged = merge_auth_spec(spec, server_url=options["server_url"])
        path.write_text(json.dumps(merged, indent=2, sort_keys=False))
        self.stdout.write(
            self.style.SUCCESS(
                f"{path}: merged {len(get_schema()['paths'])} auth paths"
            )
        )
