"""Charge a saved card server-side (MIT) via payment_charge_saved.

    manage.py charge_saved_card <saved-card-pk> <amount> <currency> \\
        --kind <kind> --description <text>

Fabricates nothing - it drives the real service (and the real gateway
outside tests), so it doubles as an ops escape hatch until a
subscription/auto-topup caller exists.
"""

from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import CommandParser


class Command(BaseCommand):
    help = "Charge a saved card server-side (merchant-initiated)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("saved_card_pk", help="SavedCard pk (uuid).")
        parser.add_argument("amount", help="Amount in major units, e.g. 25.00.")
        parser.add_argument("currency", help="Currency code, e.g. SAR.")
        parser.add_argument("--kind", required=True, help="Payment kind.")
        parser.add_argument("--description", required=True, help="Payment description.")

    def handle(self, *args: Any, **options: Any) -> None:
        from apps.payments import services
        from apps.payments.constants import Currency
        from apps.payments.constants import PaymentKind
        from apps.payments.exceptions import PaymentError
        from apps.payments.models import SavedCard

        try:
            card = SavedCard.objects.select_related("user").get(
                pk=options["saved_card_pk"]
            )
        except (SavedCard.DoesNotExist, ValueError) as exc:
            msg = f"No saved card with pk {options['saved_card_pk']}."
            raise CommandError(msg) from exc
        try:
            amount = Decimal(options["amount"])
        except InvalidOperation as exc:
            msg = f"Not a decimal amount: {options['amount']}."
            raise CommandError(msg) from exc
        try:
            currency = Currency(options["currency"])
            kind = PaymentKind(options["kind"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        try:
            payment = services.payment_charge_saved(
                user=card.user,
                saved_card=card,
                amount=amount,
                currency=currency,
                kind=kind,
                description=options["description"],
            )
        except PaymentError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(f"payment {payment.pk} -> {payment.status}")
        )
