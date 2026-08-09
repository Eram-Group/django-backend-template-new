"""Startup guard: payment credentials must match the deployment environment.

Payment providers select sandbox vs live purely from the API key -- the base URL
is identical for both (``https://api.tap.company/v2``,
``https://accept.paymob.com``). Nothing in a request distinguishes the two, and
``PAYMENT_GATEWAYS`` maps a currency to the same gateway class in every
environment, so the keys ARE the mode switch. That makes a mismatch silent in
both directions: a live key outside production charges real cards during
testing, and a test key in production takes no money while looking healthy.

Severity is deliberately split three ways, because an Error here is not
advisory: Django runs system checks before every management command, so any
container whose start command runs one (``migrate``, ``collectstatic``) fails to
start rather than serving traffic. Whether that is the right response depends
entirely on which way the mismatch runs.

  * live key OUTSIDE production -> Error, refuses to start. Test traffic would
    charge real cards, and the damage is irreversible and silent. Nothing may
    run in this state.

  * test key IN production -> Sentry error, but the service still starts.
    Nothing is captured and no money moves, so the failure is already loud at
    the checkout; taking production down on top of that makes a bad state
    worse. What it must not be is quiet, so it is reported for alerting rather
    than enforced at the door.

  * key mode unrecognisable -> Warning. ``_test_``/``_live_`` is a provider
    convention, not a guarantee; a format change on their side must not be able
    to take the service down.

The mode token is matched as a substring so both shapes work: Tap issues
``sk_live_...`` and Paymob issues country-prefixed ``egy_sk_live_...``.
"""

from collections.abc import Sequence
from typing import Any

import sentry_sdk
from django.apps.config import AppConfig
from django.conf import settings
from django.core.checks import CheckMessage
from django.core.checks import Error
from django.core.checks import Warning as CheckWarning
from django.core.checks import register

# Not credentials -- the mode substring providers embed in the key.
LIVE_TOKEN = "_live_"  # noqa: S105
TEST_TOKEN = "_test_"  # noqa: S105

#: Settings holding a credential whose value encodes the provider's mode.
#: ``PAYMOB_HMAC_SECRET`` is deliberately absent -- it carries no mode token.
MODE_BEARING_SETTINGS = (
    "TAP_SECRET_KEY",
    "PAYMOB_SECRET_KEY",
    "PAYMOB_PUBLIC_KEY",
)


def _alert_test_key_in_production(name: str) -> None:
    """Raise a Sentry error for a test key in production.

    ``capture_message`` is a no-op when the SDK was never initialised (no
    ``SENTRY_DSN``, i.e. local and test), so this needs no guard. System checks
    run once per management command, so a deploy reports this a handful of
    times rather than continuously -- enough to alert, not enough to flood.
    """
    sentry_sdk.capture_message(
        f"{name} is a test key in production - no payment can be captured.",
        level="error",
    )


def _credential(name: str) -> str | None:
    """Read a credential setting, unwrapping ``SecretStr``.

    Returns ``None`` when the provider is not configured, which the env
    contract defines as "feature disabled" -- there is nothing to check.
    """
    raw: Any = getattr(settings, name, None)
    if raw is None:
        return None
    unwrap = getattr(raw, "get_secret_value", None)
    value = unwrap() if callable(unwrap) else str(raw)
    return value or None


@register()
def check_payment_keys_match_environment(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: Any,
) -> list[CheckMessage]:
    """Refuse to start when a payment key's mode contradicts ``ENVIRONMENT``."""
    is_production = settings.ENVIRONMENT == "production"
    expected_token = LIVE_TOKEN if is_production else TEST_TOKEN
    messages: list[CheckMessage] = []

    for index, name in enumerate(MODE_BEARING_SETTINGS):
        value = _credential(name)
        if value is None:
            continue

        if expected_token in value:
            continue

        wrong_token = TEST_TOKEN if is_production else LIVE_TOKEN
        if wrong_token in value:
            if is_production:
                # Reported, not enforced -- see the module docstring. Charges
                # already fail visibly; refusing to boot would only add an
                # outage to a payments incident.
                _alert_test_key_in_production(name)
                messages.append(
                    CheckWarning(
                        f"{name} is a test key in production -- no payment can be "
                        f"captured. Reported to Sentry.",
                        hint=(
                            "Swap in the live key from the provider dashboard. "
                            "The service is deliberately still serving traffic; "
                            "checkout will fail until this is corrected."
                        ),
                        id=f"payments.W{index + 101:03d}",
                    )
                )
            else:
                messages.append(
                    Error(
                        f"{name} is a live key but ENVIRONMENT is "
                        f"{settings.ENVIRONMENT!r}.",
                        hint=(
                            "Only production may hold live keys -- anywhere else "
                            "they charge real cards during testing. Use the "
                            "provider's test key here."
                        ),
                        id=f"payments.E{index + 1:03d}",
                    )
                )
        else:
            messages.append(
                CheckWarning(
                    f"{name} does not look like a test or live key "
                    f"(expected {expected_token!r} in it).",
                    hint=(
                        "Cannot confirm this key matches ENVIRONMENT="
                        f"{settings.ENVIRONMENT!r}. Verify it in the provider "
                        "dashboard -- charges will fail if it is wrong."
                    ),
                    id=f"payments.W{index + 1:03d}",
                )
            )

    return messages
