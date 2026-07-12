"""Script validators for bilingual content fields.

Attach to the ``_ar``/``_en`` shadow columns of translation.py-managed
models so wrong-script content (the classic bilingual-admin data bug)
fails validation in admin forms AND in services' full_clean().
RegexValidator instances are deconstructible - they serialize cleanly
into migrations.
"""

from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _

# Arabic block, supplement, extended-A and presentation forms; digits,
# punctuation and whitespace stay allowed in both directions.
_ARABIC_LETTERS = "[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]"

validate_arabic_text = RegexValidator(
    regex=r"[A-Za-z]",
    inverse_match=True,
    message=_("Use Arabic text only - Latin letters are not allowed."),
    code="arabic_text_only",
)

validate_english_text = RegexValidator(
    regex=_ARABIC_LETTERS,
    inverse_match=True,
    message=_("Use English text only - Arabic letters are not allowed."),
    code="english_text_only",
)
