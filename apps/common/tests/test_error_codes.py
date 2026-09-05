"""Public error codes are an API contract.

``ApplicationError.code`` is derived from the class name, so renaming an
exception silently changes what clients branch on. This snapshot turns a
rename into a failing test: update it deliberately, alongside the clients.
"""

from importlib import import_module

from django.apps import apps

from apps.common.exceptions import ApplicationError

PUBLIC_CODES = {
    "application",
    "broadcast_audience",
    "broadcast_state",
    "customer_details_required",
    "insufficient_balance",
    "invalid_cursor",
    "notification",
    "notification_config",
    "notification_not_found",
    "notification_webhook_rejected",
    "payment",
    "payment_event_mismatch",
    "payment_gateway_unavailable",
    "payment_gateway_unknown",
    "payment_not_found",
    "payment_not_refundable",
    "payment_refund_failed",
    "payment_request_conflict",
    "saved_card_gateway_mismatch",
    "saved_card_not_found",
    "user",
    "user_not_found",
    "wallet",
    "wallet_currency_mismatch",
    "wallet_not_found",
    "webhook_rejected",
    "location",
    "invalid_coordinates",
}
#: Only in projects generated with the PostGIS knob (apps.zones).
ZONES_CODES = {"zones", "zone_file"}


def _subclasses(cls: type[ApplicationError]) -> set[type[ApplicationError]]:
    """Every subclass defined in application code (test doubles excluded)."""
    found = set(cls.__subclasses__())
    for sub in list(found):
        found |= _subclasses(sub)
    return {sub for sub in found if ".tests." not in sub.__module__}


def test_every_public_error_code_is_snapshotted() -> None:
    # Import every app's exceptions so the registry is complete.
    for app_config in apps.get_app_configs():
        if app_config.name.startswith("apps."):
            import_module(f"{app_config.name}.exceptions")
    expected = set(PUBLIC_CODES)
    if apps.is_installed("apps.zones"):
        expected |= ZONES_CODES

    codes = {
        ApplicationError.code,
        *(cls.code for cls in _subclasses(ApplicationError)),
    }
    assert codes == expected, (
        "public error codes changed - clients branch on these; update the "
        "snapshot on purpose and tell the client teams"
    )
