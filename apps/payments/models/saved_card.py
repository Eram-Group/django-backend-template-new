from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.payments.constants import GatewayName


class SavedCard(BaseModel):
    """A gateway-stored card-on-file, saved with the customer's consent.

    Only opaque provider references live here - PAN and expiry stay at the
    provider (PCI scope stays SAQ-A); brand/last4/expiry are display data.
    Rows are created by gateway webhooks (services.saved_cards) and deleted
    through saved_card_delete, which also detaches the card at the gateway
    where the provider supports it.
    """

    # PROTECT: app norm - account "deletion" is deactivation, never a row
    # delete; a stored charge credential must not vanish silently either.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="saved_cards",
        verbose_name=_("user"),
    )
    gateway = models.CharField(_("gateway"), max_length=10, choices=GatewayName)
    # The chargeable reference: Tap card_id ("card_...") / Paymob card token.
    token = models.CharField(_("token"), max_length=255)
    # Tap "cus_..." - Tap charges/deletes need it; blank for Paymob.
    gateway_customer_id = models.CharField(
        _("gateway customer id"), max_length=255, blank=True
    )
    # Tap "payment_agreement_..." - required for non-3DS (MIT) charges;
    # blank for Paymob.
    gateway_agreement_id = models.CharField(
        _("gateway agreement id"), max_length=255, blank=True
    )
    # Tap "fingerprint" - the provider's hash of the card number. The same
    # physical card keeps it across tokens/customers, so it is the dedup key;
    # blank when the provider has none (Paymob) or the lookup failed.
    fingerprint = models.CharField(_("fingerprint"), max_length=128, blank=True)
    brand = models.CharField(_("brand"), max_length=50, blank=True)
    last4 = models.CharField(_("last four digits"), max_length=4, blank=True)
    exp_month = models.PositiveSmallIntegerField(
        _("expiry month"), null=True, blank=True
    )
    exp_year = models.PositiveSmallIntegerField(_("expiry year"), null=True, blank=True)

    class Meta:
        verbose_name = _("saved card")
        verbose_name_plural = _("saved cards")
        constraints = [
            # A provider token exists once; a token resurfacing under another
            # account is reassigned by the upsert, never duplicated.
            models.UniqueConstraint(
                fields=["gateway", "token"], name="savedcard_gateway_token_unique"
            ),
            # One row per physical card per user - saved_card_store folds a
            # re-vaulted card (new token, same fingerprint) into the old row.
            models.UniqueConstraint(
                fields=["user", "gateway", "fingerprint"],
                condition=~models.Q(fingerprint=""),
                name="savedcard_user_gateway_fingerprint_unique",
            ),
        ]
        indexes = [models.Index(fields=["user", "gateway"])]

    def __str__(self) -> str:
        return f"{self.brand} **** {self.last4} ({self.gateway})"
