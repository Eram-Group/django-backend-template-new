from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = "apps.payments"
    label = "payments"
    verbose_name = "Payments"

    def ready(self) -> None:
        # Imported for its side effect: @register() only runs on import, so
        # without this the startup credential guard silently does not exist.
        from apps.payments import checks  # noqa: F401
