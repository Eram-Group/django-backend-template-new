from typing import Any

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.urls import path
from django.urls import reverse
from django.utils.translation import gettext as _
from modeltranslation.admin import TabbedTranslationAdmin

from apps.common.admin import BaseModelAdmin
from apps.common.exceptions import ApplicationError
from apps.notifications import selectors
from apps.notifications import services
from apps.notifications.admin.notificationkindconfig import change_view
from apps.notifications.admin.notificationkindconfig import list_view
from apps.notifications.admin.notificationkindconfig import permissions
from apps.notifications.admin.notificationkindconfig.form import KindConfigForm
from apps.notifications.constants import NotificationCategory
from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationKindConfig


@admin.register(NotificationKindConfig)
class NotificationKindConfigAdmin(  # type: ignore[misc]  # dual-base get_fieldsets signatures differ
    BaseModelAdmin, TabbedTranslationAdmin[NotificationKindConfig]
):
    """Per-action channel + message config.

    The changelist template is replaced by the single-page editor (every
    action as an editable card, saved per card through config_save_view ->
    services.notification_config_update). The standard change form stays as
    the no-JS fallback: TabbedTranslationAdmin swaps ``title``/``body`` for
    their ar/en shadow fields, and FieldPermissions rules auto-cover the
    shadows, so the ANNOUNCEMENT message lock holds on both tabs.
    """

    can_add = permissions.CAN_ADD
    can_change = permissions.CAN_CHANGE
    can_delete = permissions.CAN_DELETE
    field_permissions = permissions.FIELD_PERMISSIONS

    list_display = list_view.LIST_DISPLAY
    list_filter = list_view.LIST_FILTER
    list_filter_submit = list_view.LIST_FILTER_SUBMIT
    search_fields = list_view.SEARCH_FIELDS
    search_help_text = list_view.SEARCH_HELP_TEXT
    ordering = list_view.ORDERING
    list_per_page = list_view.LIST_PER_PAGE

    fieldsets = change_view.FIELDSETS
    readonly_fields = change_view.READONLY_FIELDS

    # Presentation only: the model list still renders through Django's
    # changelist_view (the sorting/filter gates keep exercising it) - the
    # template just draws editor cards instead of a result table.
    change_list_template = "admin/notifications/notificationkindconfig/change_list.html"

    def get_urls(self) -> list[Any]:
        # Prepended so it wins over the admin's `<path:object_id>` catch-all,
        # which would otherwise read "config-save" as a primary key.
        save = [
            path(
                "config-save/",
                self.admin_site.admin_view(self.config_save_view),
                name="notifications_notificationkindconfig_config_save",
            ),
        ]
        return save + list(super().get_urls())

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        extra = dict(extra_context or {})
        # Cards must not read list params: the sorting gate hits this view
        # with ?o= permutations and the page has to stay identical.
        extra["config_cards"] = self._build_cards()
        extra["config_save_url"] = reverse(
            "admin:notifications_notificationkindconfig_config_save"
        )
        extra["config_can_save"] = self.has_change_permission(request)
        response: HttpResponse = super().changelist_view(request, extra_context=extra)
        return response

    def _build_cards(self) -> list[dict[str, Any]]:
        configs = selectors.notification_config_map()
        cards: list[dict[str, Any]] = []
        for kind in sorted(NotificationKind, key=str):  # matches Meta.ordering
            config = configs[kind]
            form = KindConfigForm(instance=config, kind=kind, prefix=kind.value)
            cards.append(
                {
                    "kind": str(kind),
                    "label": kind.label,
                    "category": NotificationCategory(form.entry.category).label,
                    "marketing": form.entry.category == NotificationCategory.MARKETING,
                    "locked": form.entry.authored_per_send,
                    "needs": [f"{{{key}}}" for key in sorted(form.entry.context_keys)],
                    "form": form,
                    "change_url": reverse(
                        "admin:notifications_notificationkindconfig_change",
                        args=[config.pk],
                    ),
                }
            )
        return cards

    def config_save_view(self, request: HttpRequest) -> JsonResponse:
        """Per-card save for the editor - same seam as the change form.

        Field invariants surface as form errors (ModelForm._post_clean runs
        the model's clean()); the service adds the authored-message lock and
        is the single writer either way.
        """
        if not self.has_change_permission(request):
            raise PermissionDenied
        try:
            kind = NotificationKind(request.POST.get("kind", ""))
        except ValueError:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": [_("Unknown action.")]}},
                status=400,
            )
        config = selectors.notification_config_get(kind=kind)
        form = KindConfigForm(
            data=request.POST, instance=config, kind=kind, prefix=str(kind)
        )
        if not form.is_valid():
            return JsonResponse({"ok": False, "errors": form.errors}, status=400)
        try:
            services.notification_config_update(**form.service_kwargs())
        except ApplicationError as exc:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": [exc.message]}}, status=400
            )
        except ValidationError as exc:
            return JsonResponse({"ok": False, "errors": exc.message_dict}, status=400)
        return JsonResponse({"ok": True})
