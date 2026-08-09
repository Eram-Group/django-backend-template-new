"""Startup guard: payment credentials must match the deployment environment.

The failure this prevents is silent in both directions -- providers pick sandbox
vs live from the key alone, and the base URL is identical either way -- so these
tests pin the severity, not just the detection: a contradiction must be an Error
(which stops a container starting, so an orchestrator keeps the previous
release) while an unrecognised format must stay a Warning (a provider changing
their key format must not be able to take the service down).
"""

import pytest
from django.core.checks import CheckMessage
from django.core.checks import Error
from django.core.checks import Warning as CheckWarning
from django.test import override_settings

from apps.payments.checks import check_payment_keys_match_environment

LIVE_TAP = "sk_live_abc123"
TEST_TAP = "sk_test_abc123"
# Paymob country-prefixes its keys; the mode token is a substring, not a prefix.
LIVE_PAYMOB = "egy_sk_live_abc123"
TEST_PAYMOB = "egy_sk_test_abc123"


def run() -> list[CheckMessage]:
    return check_payment_keys_match_environment(app_configs=None)


def configured(*, environment: str, **keys: object) -> override_settings:
    """Pin ENVIRONMENT and every mode-bearing key.

    All three are set explicitly (absent ones to None) because the test settings
    carry ambient provider keys - overriding only the key under test would let
    the others report alongside it.
    """
    return override_settings(
        ENVIRONMENT=environment,
        **{
            "TAP_SECRET_KEY": None,
            "PAYMOB_SECRET_KEY": None,
            "PAYMOB_PUBLIC_KEY": None,
            **keys,
        },
    )


def test_live_key_in_production_is_accepted() -> None:
    with configured(environment="production", TAP_SECRET_KEY=LIVE_TAP):
        assert run() == []


def test_test_key_outside_production_is_accepted() -> None:
    with configured(environment="local", TAP_SECRET_KEY=TEST_TAP):
        assert run() == []


@pytest.mark.parametrize("environment", ["local", "dev"])
def test_live_key_outside_production_is_an_error(environment: str) -> None:
    """The expensive direction: test traffic charging real cards."""
    with configured(environment=environment, TAP_SECRET_KEY=LIVE_TAP):
        messages = run()

    assert len(messages) == 1
    assert isinstance(messages[0], Error)
    assert messages[0].id == "payments.E001"


def test_test_key_in_production_is_reported_but_does_not_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alert, do not enforce.

    Charges already fail visibly at checkout, so refusing to boot would only add
    an outage to a payments incident. It must not be quiet, though -- hence
    Sentry rather than silence.
    """
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "apps.payments.checks.sentry_sdk.capture_message",
        lambda message, level: captured.append((message, level)),
    )

    with configured(environment="production", TAP_SECRET_KEY=TEST_TAP):
        messages = run()

    # Warning, never Error: an Error would stop the container starting.
    assert len(messages) == 1
    assert isinstance(messages[0], CheckWarning)
    assert not isinstance(messages[0], Error)

    assert len(captured) == 1
    assert captured[0][1] == "error"
    assert "TAP_SECRET_KEY" in captured[0][0]


def test_live_key_outside_production_is_not_reported_to_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It blocks startup instead -- there is nothing to alert about later."""
    captured: list[str] = []
    monkeypatch.setattr(
        "apps.payments.checks.sentry_sdk.capture_message",
        lambda message, level: captured.append(message),
    )

    with configured(environment="dev", TAP_SECRET_KEY=LIVE_TAP):
        messages = run()

    assert isinstance(messages[0], Error)
    assert captured == []


def test_unrecognisable_key_is_only_a_warning() -> None:
    """A provider changing their key format must not stop the service booting."""
    with configured(environment="production", TAP_SECRET_KEY="opaque-token"):
        messages = run()

    assert len(messages) == 1
    assert isinstance(messages[0], CheckWarning)
    assert not isinstance(messages[0], Error)


def test_unconfigured_provider_is_not_checked() -> None:
    """An absent key means the provider is disabled, not misconfigured."""
    with configured(environment="production"):
        assert run() == []


def test_country_prefixed_key_is_classified_by_its_mode_token() -> None:
    """Paymob keys are `egy_sk_live_...`, so a prefix match would miss them."""
    with configured(environment="local", PAYMOB_SECRET_KEY=LIVE_PAYMOB):
        assert isinstance(run()[0], Error)

    with configured(environment="local", PAYMOB_SECRET_KEY=TEST_PAYMOB):
        assert run() == []


def test_every_mode_bearing_key_is_reported_separately() -> None:
    """One misconfigured provider must not mask another."""
    with configured(
        environment="local",
        TAP_SECRET_KEY=LIVE_TAP,
        PAYMOB_SECRET_KEY=LIVE_PAYMOB,
        PAYMOB_PUBLIC_KEY="egy_pk_live_abc123",
    ):
        messages = run()

    assert len(messages) == 3
    assert {m.id for m in messages} == {
        "payments.E001",
        "payments.E002",
        "payments.E003",
    }


def test_secretstr_credentials_are_unwrapped() -> None:
    """Settings hold pydantic SecretStr; str() of one is '**********'."""
    from pydantic import SecretStr

    with configured(environment="local", TAP_SECRET_KEY=SecretStr(LIVE_TAP)):
        assert isinstance(run()[0], Error)


def test_check_is_registered() -> None:
    """A check that is never registered silently protects nothing."""
    from django.core.checks import registry

    assert check_payment_keys_match_environment in registry.registry.get_checks()
