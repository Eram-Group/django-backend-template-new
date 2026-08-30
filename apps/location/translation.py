"""modeltranslation registrations (autodiscovered as <app>.translation).

Registered in the same change that creates the model, so the initial
CreateModel already carries the name_ar/name_en shadow columns.
"""

from modeltranslation.translator import TranslationOptions
from modeltranslation.translator import register

from apps.location.models import Country
from apps.location.models import Zone


@register(Country)
class CountryTranslationOptions(TranslationOptions):
    fields = ("name",)
    # Both names come from CLDR at load time and stay required on edit.
    required_languages = ("ar", "en")


@register(Zone)
class ZoneTranslationOptions(TranslationOptions):
    fields = ("name",)
    # Loaded names may be empty in the source file: the load service fills
    # the gap with the code (and leaves the row inactive) so this holds.
    required_languages = ("ar", "en")
