"""Merge allauth's headless auth spec into an exported ninja OpenAPI file.

The Apidog sync uploads ONE spec - without this merge the entire auth API
never reaches the published docs. Fixes applied while merging:

- allauth's spec ships without operationIds (breaks importers/generators):
  deterministic ids derive from method+path.
- auth operations get "Authentication / <tag>" tags so they group cleanly.
- a global Accept-Language (ar|en) header parameter is attached to every
  operation - localized responses are a headline feature and must be
  discoverable in the docs.

Pinned allauth internals: allauth.headless.spec.internal.schema.get_schema
(covered by tests, same pin style as config/api/auth.py's sessionkit).
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from allauth.headless.spec.internal.schema import get_schema
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
_ACCEPT_LANGUAGE_REF = "#/components/parameters/AcceptLanguage"
_ACCEPT_LANGUAGE_PARAM = {
    "name": "Accept-Language",
    "in": "header",
    "required": False,
    "description": "Response language (Arabic-first: ar is the default).",
    "schema": {"type": "string", "enum": ["ar", "en"], "default": "ar"},
}


def _operation_id(method: str, path: str) -> str:
    digest = hashlib.md5(  # noqa: S324 - non-cryptographic, stable id
        f"{method}:{path}".encode()
    ).hexdigest()
    return f"auth_{digest[:12]}"


def merge_auth_spec(
    spec: dict[str, Any], *, server_url: str | None = None
) -> dict[str, Any]:
    auth = get_schema()

    for path, methods in auth["paths"].items():
        if path in spec["paths"]:
            msg = f"auth path collides with the API spec: {path}"
            raise CommandError(msg)
        for method, operation in methods.items():
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation.setdefault("operationId", _operation_id(method, path))
            operation["tags"] = [
                f"Authentication / {tag}"
                for tag in operation.get("tags", ["Authentication"])
            ]
        spec["paths"][path] = methods

    components = spec.setdefault("components", {})
    for section, entries in auth.get("components", {}).items():
        target = components.setdefault(section, {})
        for name, entry in entries.items():
            if name in target and target[name] != entry:
                msg = f"auth component collides with the API spec: {section}.{name}"
                raise CommandError(msg)
            target.setdefault(name, entry)

    components.setdefault("parameters", {})["AcceptLanguage"] = _ACCEPT_LANGUAGE_PARAM
    for methods in spec["paths"].values():
        for method, operation in methods.items():
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            parameters = operation.setdefault("parameters", [])
            if not any(
                isinstance(parameter, dict)
                and parameter.get("$ref") == _ACCEPT_LANGUAGE_REF
                for parameter in parameters
            ):
                parameters.append({"$ref": _ACCEPT_LANGUAGE_REF})

    if server_url:
        spec["servers"] = [{"url": server_url}]
    return spec


class Command(BaseCommand):
    help = "Merge the allauth headless auth spec into an exported openapi.json."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--input", default="openapi.json")
        parser.add_argument("--output", default=None, help="defaults to --input")
        parser.add_argument(
            "--server-url",
            default=None,
            help="optional servers[] entry for the deployed base URL",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        input_path = Path(options["input"])
        output_path = Path(options["output"] or options["input"])
        spec = json.loads(input_path.read_text())
        merged = merge_auth_spec(spec, server_url=options["server_url"])
        output_path.write_text(json.dumps(merged, indent=2, sort_keys=False))
        self.stdout.write(
            self.style.SUCCESS(
                f"{output_path}: merged {len(get_schema()['paths'])} auth paths"
            )
        )
