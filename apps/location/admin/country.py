from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from django.contrib import admin
from django.contrib import messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TabbedTranslationAdmin
from unfold.decorators import action
from unfold.decorators import display

from apps.common.admin import BaseModelAdmin
from apps.common.admin import FieldPermissions
from apps.location import selectors
from apps.location import services
from apps.location.admin.forms import CountryLoadForm
from apps.location.admin.resources import CountryResource
from apps.location.iso import iso_countries
from apps.location.models import Country

if TYPE_CHECKING:
    from django.contrib.admin.options import _FieldsetSpec


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


@admin.register(Country)
class CountryAdmin(BaseModelAdmin, TabbedTranslationAdmin[Country]):
    """Countries are loaded, not typed.

    The changelist's "Load countries" button (an unfold list action, so it
    is mounted and permission-gated by the framework) opens a sheet listing
    every ISO country the iso module can describe completely; the picked
    ones are created through ``services.countries_load`` and their flags
    arrive via the download task. The change form edits names, flag and
    is_active only - every ISO-derived column is readonly.
    """

    # Capability + field decisions for the Country admin.
    #
    # Rows are born from the load sheet (ISO data), so the generic add form is
    # off; deletion is off too - deactivate instead, future FKs PROTECT the row.

    can_add = False  # the "Load countries" sheet is the only creation road
    can_change = True  # names, flag, is_active
    can_delete = False  # deactivate; a deleted market would orphan future FKs

    # ISO-derived columns are unconditionally readonly (change_view.READONLY_FIELDS).
    field_permissions = FieldPermissions()
    list_display = (
        flag_thumbnail,
        "name",
        "code",
        "dial_code",
        "currency",
        "is_active",
    )
    list_filter = ("is_active", "currency")
    list_filter_submit = False
    search_fields = ("code", "name_ar", "name_en")
    search_help_text = _("Search by code or name (Arabic or English).")
    ordering = ("name",)
    list_per_page = 50
    # TabbedTranslationAdmin is a typed base: name the TypedDict shape here.
    fieldsets: ClassVar[_FieldsetSpec] = (
        (None, {"fields": ("code", "alpha_3", "name", "flag", "is_active")}),
        ("Phone", {"fields": ("dial_code", "phone_example", "max_phone_length")}),
        ("Currency", {"fields": ("currency",)}),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )

    # ISO-derived identity: copied from the libraries at load, never retyped.
    readonly_fields = (
        "code",
        "alpha_3",
        "dial_code",
        "phone_example",
        "max_phone_length",
        "currency",
    )

    resource_classes = [CountryResource]

    actions_list = ["load_countries"]
    actions = ["fetch_flags"]

    def has_load_permission(self, request: HttpRequest, object_id: Any = None) -> bool:
        return request.user.has_perm("location.add_country")

    @action(
        description=_("Load countries"),
        url_path="load",
        permissions=["load"],
        icon="public",
    )
    def load_countries(self, request: HttpRequest) -> HttpResponse:
        """GET: the sheet. POST: create the picked codes, back to the list."""
        changelist_url = reverse("admin:location_country_changelist")
        is_post = request.method == "POST"
        # Bound on the method, not on ``request.POST or None``: an empty
        # selection must come back as a form error, not a blank sheet.
        form = CountryLoadForm(request.POST) if is_post else CountryLoadForm()
        if is_post and form.is_valid():
            created = services.countries_load(codes=form.cleaned_data["codes"])
            messages.success(
                request, _("Loaded %(count)d countries.") % {"count": len(created)}
            )
            return redirect(changelist_url)
        loaded_codes = selectors.country_loaded_codes()
        context = {
            **self.admin_site.each_context(request),
            "title": _("Load countries"),
            "form": form,
            "countries": iso_countries(),
            "loaded_codes": loaded_codes,
            "loaded_count": len(loaded_codes),
            "changelist_url": changelist_url,
        }
        return render(request, "admin/location/country/load.html", context)

    @admin.action(description=_("Fetch flags for selected countries"))
    def fetch_flags(self, request: HttpRequest, queryset: QuerySet[Country]) -> None:
        count = services.country_flags_fetch(countries=queryset)
        messages.success(
            request,
            _("Flag download queued for %(count)d countries.") % {"count": count},
        )
