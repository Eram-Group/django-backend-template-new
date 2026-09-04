from typing import Any

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import transaction
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
from apps.notifications.admin.notificationkindconfig.form import KindConfigForm
from apps.notifications.admin.notificationkindconfig.resource import (
    NotificationKindConfigResource,
)
from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationKindConfig


@admin.register(NotificationKindConfig)
class NotificationKindConfigAdmin(
    BaseModelAdmin, TabbedTranslationAdmin[NotificationKindConfig]
):
    """Per-action channel + message config.

    The changelist template is replaced by the single-page editor (every
    action as an editable card; one Save posts every edited card to
    config_save_view -> services.notification_config_update, atomically).
    The standard change form stays as the no-JS fallback:
    TabbedTranslationAdmin swaps ``title``/``body`` for their ar/en shadow
    fields, and FieldPermissions rules auto-cover the shadows, so the
    ANNOUNCEMENT message lock holds on both tabs.
    """

    resource_classes = [NotificationKindConfigResource]

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
            if catalog_entry(kind).authored_per_send:
                # The broadcast composer: message AND channels are picked per
                # broadcast, so the row has nothing an operator sets here.
                continue
            config = configs.get(kind)
            missing = config is None
            # A kind with no row yet renders unsaved, prefilled with the
            # catalog's recommended values and flagged so the operator reviews
            # it; its first Save creates the row (a GET never writes).
            form = KindConfigForm(
                instance=config,
                initial=KindConfigForm.starting_values(kind) if missing else None,
                kind=kind,
                prefix=kind.value,
            )
            cards.append(
                {
                    "kind": str(kind),
                    "new": missing,
                    "label": kind.label,
                    "vars": form.variables(),
                    "form": form,
                }
            )
        return cards

    def config_save_view(self, request: HttpRequest) -> JsonResponse:
        """One save for every edited card - all or nothing.

        The page posts the fields of each dirty card under its own kind
        prefix plus one ``kind`` value per card; a kind with no row yet is
        created by its first save. Every form is validated
        first (ModelForm._post_clean runs the model's clean()); a single
        invalid card fails the whole request with errors keyed by kind, and
        the writes happen in one transaction through the service, which
        stays the single writer.
        """
        if not self.has_change_permission(request):
            raise PermissionDenied
        forms: list[KindConfigForm] = []
        errors: dict[str, Any] = {}
        configs = selectors.notification_config_map()
        for raw in request.POST.getlist("kind"):
            try:
                kind = NotificationKind(raw)
                if catalog_entry(kind).authored_per_send:
                    raise ValueError(raw)  # noqa: TRY301 - same envelope as unknown
            except ValueError:
                return JsonResponse(
                    {"ok": False, "errors": {"__all__": [_("Unknown action.")]}},
                    status=400,
                )
            form = KindConfigForm(
                data=request.POST,
                instance=configs.get(kind),  # None = this save creates the row
                kind=kind,
                prefix=str(kind),
            )
            if not form.is_valid():
                errors[str(kind)] = form.errors
            forms.append(form)
        if not forms:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": [_("Nothing to save.")]}},
                status=400,
            )
        if errors:
            return JsonResponse({"ok": False, "errors": errors}, status=400)
        try:
            with transaction.atomic():
                for form in forms:
                    services.notification_config_update(**form.service_kwargs())
        except ApplicationError as exc:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": [exc.message]}}, status=400
            )
        except ValidationError as exc:
            return JsonResponse(
                {"ok": False, "errors": {"__all__": exc.messages}}, status=400
            )
        return JsonResponse({"ok": True, "saved": [str(form.kind) for form in forms]})
