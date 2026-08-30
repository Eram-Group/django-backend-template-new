"""Country reference data - one row per market the product is offered in."""

from django.db import models
from django.utils.translation import gettext_lazy as _
from modeltranslation.manager import MultilingualManager

from apps.common.models import BaseModel


class Country(BaseModel):
    """An ISO 3166-1 country loaded from the admin sheet (apps/location/iso.py).

    Rows are created ONLY by ``services.countries_load`` - every reference
    column is copied from the ISO libraries, never typed. Operators edit the
    names, upload a custom flag, or deactivate; ``is_active`` is the public
    filter, deletion is off (future FKs PROTECT).

    ``objects`` is declared as MultilingualManager up front for the
    django-stubs plugin (see NotificationKindConfig); ``Meta.ordering`` on
    ``name`` resolves to the active language's column.
    """

    objects = MultilingualManager["Country"]()

    code = models.CharField(_("code"), max_length=2, unique=True)
    alpha_3 = models.CharField(_("alpha-3 code"), max_length=3, unique=True)
    name = models.CharField(_("name"), max_length=100)
    dial_code = models.CharField(_("dial code"), max_length=5)
    phone_example = models.CharField(_("phone example"), max_length=32)
    # Digit count of the ISO example number - a client input hint only.
    max_phone_length = models.PositiveSmallIntegerField(_("max phone length"))
    # CLDR tender currency (ISO 4217). Deliberately not payments.Currency:
    # that enum is the set our gateways can charge, a subset of this.
    currency = models.CharField(_("currency"), max_length=3, db_index=True)
    flag = models.ImageField(_("flag"), upload_to="location/flags/", blank=True)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    # modeltranslation shadow columns (translation.py registers them); bare
    # annotations only - Django ignores them, mypy learns the attributes.
    name_ar: str | None
    name_en: str | None

    class Meta:
        ordering = ["name"]
        verbose_name = _("country")
        verbose_name_plural = _("countries")

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        self.code = self.code.upper()
        self.alpha_3 = self.alpha_3.upper()
        self.currency = self.currency.upper()
