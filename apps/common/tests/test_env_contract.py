"""Env contract gate: .env.example mirrors config/env.py exactly.

Every Env field is required with no code default, so the example file IS the
documentation of the configuration surface - a field added to one without
the other fails here (same-change rule from CLAUDE.md).
"""

from pathlib import Path

from config.env import Env

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"


def _example_keys() -> list[str]:
    keys = []
    for line in ENV_EXAMPLE.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            keys.append(stripped.split("=", 1)[0].strip())
    return keys


def test_env_example_matches_env_fields_exactly() -> None:
    example = _example_keys()
    fields = list(Env.model_fields)

    missing_from_example = [f for f in fields if f not in example]
    stale_in_example = [k for k in example if k not in fields]
    assert not missing_from_example, (
        f"Env fields absent from .env.example: {missing_from_example}"
    )
    assert not stale_in_example, (
        f".env.example keys with no Env field: {stale_in_example}"
    )
    assert example == fields, (
        ".env.example key order must match config/env.py field order "
        "(keeps the two reviewable side by side)."
    )
