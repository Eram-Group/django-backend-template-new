"""Env contract gate: .env.example mirrors config/env.py exactly.

Every Env field is required with no code default, so the example file IS the
documentation of the configuration surface - a field added to one without
the other fails here (same-change rule from CLAUDE.md).
"""

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

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


def _looks_like_a_comment(value: object) -> bool:
    """True when a parsed env value is really a stray trailing comment.

    SecretStr survives model_dump as an object rather than a str, so unwrap it
    first - otherwise the check skips exactly the credential fields most
    likely to carry an explanatory comment.
    """
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return isinstance(value, str) and value.lstrip().startswith("#")


def test_env_example_parses_with_no_comment_values() -> None:
    """.env.example must load as a complete, valid config.

    CI is the only consumer that runs `cp .env.example .env`, so a malformed
    line here is invisible locally and fails a job instead. A trailing
    `KEY= # note` is the trap: env_ignore_empty makes a bare `KEY=` mean
    unset, but the comment makes it a real VALUE, silently defeating every
    `is None` not-configured guard downstream.
    """
    env = Env(_env_file=str(ENV_EXAMPLE))

    commented = sorted(
        key for key, value in env.model_dump().items() if _looks_like_a_comment(value)
    )
    assert not commented, (
        f"Values in .env.example parsed as comments: {commented}. "
        "Move the note to its own line and leave the value empty."
    )


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_blank_optional_secret_normalises_to_none(blank: str) -> None:
    """A blank optional secret must read as unset, not as a blank SecretStr.

    env_ignore_empty only catches an exactly-empty value, so whitespace would
    otherwise parse as SecretStr('   ') and slip past every `is None`
    not-configured guard - deferring the failure to send time, or letting
    paymob sign a webhook with a blank key instead of refusing it.
    """
    env = Env(_env_file=str(ENV_EXAMPLE), TAP_SECRET_KEY=blank)
    assert env.TAP_SECRET_KEY is None

    env = Env(_env_file=str(ENV_EXAMPLE), PAYMOB_HMAC_SECRET=blank)
    assert env.PAYMOB_HMAC_SECRET is None

    env = Env(_env_file=str(ENV_EXAMPLE), PAYMOB_API_KEY=blank)
    assert env.PAYMOB_API_KEY is None


def test_blank_normalisation_leaves_lists_and_required_fields_alone() -> None:
    """Only optional fields normalise - lists still collapse, required stay strict."""
    env = Env(_env_file=str(ENV_EXAMPLE), PAYMOB_INTEGRATION_IDS="   ")
    assert env.PAYMOB_INTEGRATION_IDS == []  # an empty list, never None

    env = Env(_env_file=str(ENV_EXAMPLE), PAYMOB_INTEGRATION_IDS="1, 2 ,3")
    assert env.PAYMOB_INTEGRATION_IDS == [1, 2, 3]

    # A required field is left untouched by the normaliser (blank-vs-missing
    # for required fields is a separate, pre-existing question).
    assert Env(_env_file=str(ENV_EXAMPLE), ADMIN_URL="admin/").ADMIN_URL == "admin/"


@pytest.mark.parametrize(
    ("environment", "field", "value"),
    [
        ("production", "TAP_SECRET_KEY", "sk_live_abc"),
        ("production", "PAYMOB_SECRET_KEY", "egy_sk_live_abc"),
        ("production", "PAYMOB_PUBLIC_KEY", "egy_pk_live_abc"),
        ("local", "TAP_SECRET_KEY", "sk_test_abc"),
        ("local", "PAYMOB_SECRET_KEY", "egy_sk_test_abc"),
        ("dev", "PAYMOB_PUBLIC_KEY", "egy_pk_test_abc"),
    ],
)
def test_payment_key_matching_environment_is_accepted(
    environment: str, field: str, value: str
) -> None:
    overrides: dict[str, Any] = {"ENVIRONMENT": environment, field: value}
    env = Env(_env_file=str(ENV_EXAMPLE), **overrides)
    assert getattr(env, field) is not None


@pytest.mark.parametrize(
    ("environment", "field", "value", "mode"),
    [
        # Test keys deployed: every checkout a sandbox no-op nobody notices.
        ("production", "TAP_SECRET_KEY", "sk_test_abc", "live"),
        ("production", "PAYMOB_SECRET_KEY", "egy_sk_test_abc", "live"),
        ("production", "PAYMOB_PUBLIC_KEY", "egy_pk_test_abc", "live"),
        # Live keys on a laptop / dev box: real charges.
        ("local", "TAP_SECRET_KEY", "sk_live_abc", "test"),
        ("local", "PAYMOB_SECRET_KEY", "egy_sk_live_abc", "test"),
        ("dev", "PAYMOB_PUBLIC_KEY", "egy_pk_live_abc", "test"),
        # Says neither: refused as well.
        ("production", "TAP_SECRET_KEY", "not-a-key", "live"),
    ],
)
def test_payment_key_mismatching_environment_is_refused(
    environment: str, field: str, value: str, mode: str
) -> None:
    """Fails at import, names the field, and never echoes the key."""
    overrides: dict[str, Any] = {"ENVIRONMENT": environment, field: value}
    with pytest.raises(ValueError, match=f"{field} is not a {mode} key") as excinfo:
        Env(_env_file=str(ENV_EXAMPLE), **overrides)
    assert value not in str(excinfo.value)
