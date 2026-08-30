"""Changelist configuration for Country."""

from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.decorators import display

from apps.location.models import Country


@display(description=_("flag"))
def flag_thumbnail(obj: Country) -> str:
    # A download can fail (or be pending), so the slot is empty rather than
    # a crashing ``.url`` on an unset file.
    if not obj.flag:
        return ""
    return format_html(
        '<img src="{}" alt="" style="height:20px;width:auto;border-radius:2px">',
        obj.flag.url,
    )


LIST_DISPLAY = (flag_thumbnail, "name", "code", "dial_code", "currency", "is_active")
LIST_FILTER = ("is_active", "currency")
LIST_FILTER_SUBMIT = False
SEARCH_FIELDS = ("code", "name_ar", "name_en")
SEARCH_HELP_TEXT = _("Search by code or name (Arabic or English).")
ORDERING = ("name",)
LIST_PER_PAGE = 50
