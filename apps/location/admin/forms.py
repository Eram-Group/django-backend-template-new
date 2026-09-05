"""Codes picked on the load sheet, limited to what is loadable and not loaded."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.location import selectors
from apps.location.iso import iso_countries


class CountryLoadForm(forms.Form):
    codes = forms.MultipleChoiceField(
        label=_("Countries"),
        error_messages={"required": _("Pick at least one country to load.")},
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        loaded = selectors.country_loaded_codes()
        self.fields["codes"].choices = [  # type: ignore[attr-defined]
            (country.code, country.name_en)
            for country in iso_countries()
            if country.code not in loaded
        ]
