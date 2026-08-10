from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.payments.constants import GatewayName


class SavedCard(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="saved_cards",
        verbose_name=_("user"),
    )
    gateway = models.CharField(_("gateway"), max_length=10, choices=GatewayName)
    token = models.CharField(_("token"), max_length=255)
    gateway_customer_id = models.CharField(
        _("gateway customer id"), max_length=255, blank=True
    )
    gateway_agreement_id = models.CharField(
        _("gateway agreement id"), max_length=255, blank=True
    )
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
            models.UniqueConstraint(
                fields=["gateway", "token"], name="savedcard_gateway_token_unique"
            ),
        ]
        indexes = [models.Index(fields=["user", "gateway"])]

    def __str__(self) -> str:
        return f"{self.brand} **** {self.last4} ({self.gateway})"
