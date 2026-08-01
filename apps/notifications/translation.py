"""modeltranslation registrations (autodiscovered as <app>.translation).

Registering here, in the same change that creates the model, is load-bearing:
registration runs in app ready(), before makemigrations autodetects, so the
initial CreateModel already carries the title_ar/title_en/body_ar/body_en
shadow columns instead of a follow-up AddField migration.
"""

from modeltranslation.translator import TranslationOptions
from modeltranslation.translator import register

from apps.notifications.models import NotificationKindConfig


@register(NotificationKindConfig)
class NotificationKindConfigTranslationOptions(TranslationOptions):
    fields = ("title", "body")
    # Arabic-first product: copy must exist in BOTH languages - the shadow
    # fields become form-required while their columns stay nullable.
    required_languages = ("ar", "en")
