"""The load sheet's inputs: which country, which GeoJSON file.

Parsing lives in the service (one error road); the form only guarantees a
readable upload of sane size and a loadable country.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.location import selectors
from apps.location.geojson import MAX_DOCUMENT_BYTES
from apps.location.models import Country


class ZoneLoadForm(forms.Form):
    country = forms.ModelChoiceField(
        queryset=Country.objects.none(),
        label=_("Country"),
        help_text=_("Every feature in the file must carry this country's code."),
    )
    document = forms.FileField(
        label=_("GeoJSON file"),
        help_text=_(
            "A FeatureCollection whose features carry name_en, name_ar, "
            "country_code, region_code and zone_code, with Polygon or "
            "MultiPolygon geometries (WGS84)."
        ),
        widget=forms.FileInput(
            attrs={"accept": ".geojson,.json,application/geo+json,application/json"}
        ),
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.fields["country"].queryset = selectors.country_list_active()  # type: ignore[attr-defined]

    def clean_document(self) -> bytes:
        upload = self.cleaned_data["document"]
        if upload.size > MAX_DOCUMENT_BYTES:
            raise forms.ValidationError(
                _("The file is larger than %(limit)d MB."),
                params={"limit": MAX_DOCUMENT_BYTES // (1024 * 1024)},
            )
        content: bytes = upload.read()
        return content
