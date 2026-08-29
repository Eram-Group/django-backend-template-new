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
#: What every deployed environment must add on top of the example file.
DEPLOYED: dict[str, Any] = {
    "AWS_STORAGE_BUCKET_NAME": "bucket",
    "AWS_S3_REGION_NAME": "eu-central-1",
    "AWS_S3_CUSTOM_DOMAIN": "d123.cloudfront.net",
    "AWS_SES_REGION": "eu-central-1",
    "SENTRY_DSN": "https://key@sentry.invalid/1",
    "SENTRY_RELEASE": "abc123",
}


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


def test_deployed_environment_requires_infra_fields() -> None:
    """S3, SES and Sentry are not optional once deployed - fail at import."""
    with pytest.raises(ValueError, match="required when ENVIRONMENT=dev") as excinfo:
        Env(_env_file=str(ENV_EXAMPLE), ENVIRONMENT="dev")
    assert "SENTRY_DSN" in str(excinfo.value)
    assert "AWS_STORAGE_BUCKET_NAME" in str(excinfo.value)


def test_local_environment_leaves_infra_fields_unset() -> None:
    env = Env(_env_file=str(ENV_EXAMPLE))
    assert env.SENTRY_DSN is None
    assert env.PAYMOB_INTEGRATION_IDS == []  # empty list, never None


@pytest.mark.parametrize(
    ("environment", "field", "value"),
    [
        ("production", "TAP_SECRET_KEY", "sk_live_abc"),
        ("production", "PAYMOB_SECRET_KEY", "egy_sk_live_abc"),
        ("production", "PAYMOB_PUBLIC_KEY", "egy_pk_live_abc"),
        ("local", "TAP_SECRET_KEY", "sk_test_abc"),
        ("local", "PAYMOB_SECRET_KEY", "egy_sk_test_abc"),
        ("dev", "PAYMOB_PUBLIC_KEY", "egy_pk_test_abc"),
        ("staging", "TAP_SECRET_KEY", "sk_test_abc"),
    ],
)
def test_payment_key_matching_environment_is_accepted(
    environment: str, field: str, value: str
) -> None:
    overrides: dict[str, Any] = {"ENVIRONMENT": environment, field: value}
    env = Env(_env_file=str(ENV_EXAMPLE), **DEPLOYED, **overrides)
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
        ("staging", "TAP_SECRET_KEY", "sk_live_abc", "test"),
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
        Env(_env_file=str(ENV_EXAMPLE), **DEPLOYED, **overrides)
    assert value not in str(excinfo.value)
