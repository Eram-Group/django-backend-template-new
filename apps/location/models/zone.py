"""Zone - a named service area (MultiPolygon) inside a country."""

from django.contrib.gis.db import models as gis_models
from django.db import models
from django.utils.translation import gettext_lazy as _
from modeltranslation.manager import MultilingualManager

from apps.common.models import BaseModel
from apps.location.models.country import Country


class Zone(BaseModel):
    """One polygon of the operational map, loaded from a GeoJSON file.

    Rows are born ONLY from the admin load sheet (``services.zones_load``):
    the file's feature properties supply the names and the codes, the
    geometry lands in PostGIS (GiST index; ``zone_for_point`` is one
    ``ST_Contains`` query). Operators edit names, ``region_code`` and
    ``is_active``; ``code`` and ``geometry`` are identity and come back from
    a re-load of the file. Deletion is allowed - nothing references a zone
    yet.

    ``objects`` is declared as MultilingualManager up front for the
    django-stubs plugin (see Country).
    """

    objects = MultilingualManager["Zone"]()

    country = models.ForeignKey(
        Country,
        on_delete=models.PROTECT,
        related_name="zones",
        verbose_name=_("country"),
    )
    # The file's region_code (e.g. "AZ", "CA_E") - a grouping key, no model.
    region_code = models.CharField(_("region code"), max_length=20, db_index=True)
    # "<country>-<region>-<zone_code>" lowercased: stable identity across
    # re-loads, unique across countries.
    code = models.SlugField(_("code"), max_length=80, unique=True)
    # TextField: source names can be comma-joined district lists (1k chars).
    name = models.TextField(_("name"))
    geometry = gis_models.MultiPolygonField(_("geometry"), srid=4326)
    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    # modeltranslation shadow columns (translation.py registers them); bare
    # annotations only - Django ignores them, mypy learns the attributes.
    name_ar: str | None
    name_en: str | None

    class Meta:
        # Unique -> deterministic: the admin gates and the "lowest code wins"
        # rule of zone_for_point both rely on it.
        ordering = ["code"]
        verbose_name = _("zone")
        verbose_name_plural = _("zones")

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        self.code = self.code.strip().lower()
        self.region_code = self.region_code.strip().upper()
