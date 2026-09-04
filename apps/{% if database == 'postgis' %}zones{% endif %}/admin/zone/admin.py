from typing import Any
from typing import cast

from django.contrib import admin
from django.contrib import messages
from django.db.models import Model
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import format_html_join
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TabbedTranslationAdmin
from unfold.decorators import action
from unfold.decorators import display

from apps.common.admin import BaseModelAdmin
from apps.zones import selectors
from apps.zones import services
from apps.zones.admin.zone.load_form import ZoneLoadForm
from apps.zones.admin.zone.resource import ZoneResource
from apps.zones.exceptions import ZoneFileError
from apps.zones.models import Zone

OVERLAP_REPORT_LIMIT = 20


@admin.register(Zone)
class ZoneAdmin(BaseModelAdmin, TabbedTranslationAdmin[Zone]):
    """Zones are loaded, not drawn.

    The changelist's "Load zones" button (an unfold list action, so it is
    mounted and permission-gated by the framework) takes a country and a
    GeoJSON FeatureCollection; ``services.zones_load`` upserts one zone per
    feature. The change form edits names, region and is_active - the
    geometry is shown as a readonly summary (a map widget would need the
    OpenLayers CDN and OSM tiles allowed through SECURE_CSP; deliberately
    not done here).
    """

    resource_classes = [ZoneResource]

    actions_list = ["load_zones"]
    actions = ["find_overlaps"]

    def has_load_permission(self, request: HttpRequest, object_id: Any = None) -> bool:
        return request.user.has_perm("zones.add_zone")

    def save_model(
        self, request: HttpRequest, obj: Model, form: ModelForm[Any], change: bool
    ) -> None:
        """Operator edits go through ``services.zone_update`` (the allowlist +
        full_clean gate), never ``obj.save()``; rows are only ever created by
        the load sheet."""
        services.zone_update(
            zone=cast("Zone", obj),
            data={field: form.cleaned_data[field] for field in form.changed_data},
        )

    @action(
        description=_("Load zones"),
        url_path="load",
        permissions=["load"],
        icon="map",
    )
    def load_zones(self, request: HttpRequest) -> HttpResponse:
        """GET: the sheet. POST: upsert the file's features, back to the list."""
        changelist_url = reverse("admin:zones_zone_changelist")
        is_post = request.method == "POST"
        form = ZoneLoadForm(request.POST, request.FILES) if is_post else ZoneLoadForm()
        if is_post and form.is_valid():
            try:
                result = services.zones_load(
                    country=form.cleaned_data["country"],
                    document=form.cleaned_data["document"],
                )
            except ZoneFileError as exc:
                form.add_error("document", exc.message)
            else:
                messages.success(
                    request,
                    _(
                        "Loaded %(created)d new and updated %(updated)d zones; "
                        "%(unnamed)d need a name before activation."
                    )
                    % {
                        "created": result.created,
                        "updated": result.updated,
                        "unnamed": result.unnamed,
                    },
                )
                return redirect(changelist_url)
        context = {
            **self.admin_site.each_context(request),
            "title": _("Load zones"),
            "form": form,
            "changelist_url": changelist_url,
        }
        return render(request, "admin/zones/zone/load.html", context)

    @admin.action(description=_("Find overlaps for selected zones"))
    def find_overlaps(self, request: HttpRequest, queryset: QuerySet[Zone]) -> None:
        """Report which of the selected zones share area with another zone."""
        overlapping = [
            zone.code
            for zone in queryset
            if selectors.zone_overlaps(zone=zone).exists()
        ]
        if not overlapping:
            messages.success(request, _("No overlaps among the selected zones."))
            return
        listed = ", ".join(overlapping[:OVERLAP_REPORT_LIMIT])
        if len(overlapping) > OVERLAP_REPORT_LIMIT:
            listed += " …"
        messages.warning(
            request,
            _("%(count)d selected zones overlap another zone: %(codes)s")
            % {"count": len(overlapping), "codes": listed},
        )

    @display(description=_("Geometry"))
    def geometry_details(self, obj: Zone) -> str:
        geometry = obj.geometry
        xmin, ymin, xmax, ymax = geometry.extent
        centroid = geometry.centroid
        lines = (
            _("Polygons: %(count)d") % {"count": geometry.num_geom},
            _("Vertices: %(count)d") % {"count": geometry.num_coords},
            _("Bounding box (lng, lat): %(box)s")
            % {"box": f"{xmin:.5f}, {ymin:.5f} → {xmax:.5f}, {ymax:.5f}"},
            _("Centroid (lat, lng): %(point)s")
            % {"point": f"{centroid.y:.5f}, {centroid.x:.5f}"},
        )
        return format_html_join(mark_safe("<br>"), "{}", ((line,) for line in lines))
