from django.db import models
from django.utils.translation import gettext_lazy as _


class Language(models.TextChoices):
    ARABIC = "ar", _("Arabic")
    ENGLISH = "en", _("English")
