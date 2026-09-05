from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar
from typing import cast

from django.contrib import admin
from django.db.models import Model
from django.forms import ModelForm
from django.http import HttpRequest
from django.utils import translation
from django.utils.html import format_html
from django.utils.html import format_html_join
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TabbedTranslationAdmin
from unfold.decorators import display

from apps.common.admin import AdminContext
from apps.common.admin import BaseModelAdmin
from apps.common.admin import FieldPermissions
from apps.notifications import selectors
from apps.notifications import services
from apps.notifications.admin.forms import SAMPLE_VALUES
from apps.notifications.admin.forms import KindConfigAdminForm
from apps.notifications.admin.resources import NotificationKindConfigResource
from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationKindConfig
from apps.notifications.models.kind_config import MESSAGE_FIELDS

if TYPE_CHECKING:
    from django.contrib.admin.options import _FieldsetSpec


def message_locked(context: AdminContext) -> bool:
    """authored_per_send kinds (the broadcast composer's ANNOUNCEMENT) keep
    their passthrough title/body - the message is written per broadcast.

    A row is needed to know the kind (the add view has none - can_add is
    False today, and the rule must not 500 if that ever flips); every row's
    kind is a catalog kind - the field is read-only and the rows were born
    from the catalog.
    """
    if context.obj is None:
        return False
    config = cast("NotificationKindConfig", context.obj)
    return catalog_entry(NotificationKind(config.kind)).authored_per_send


@admin.register(NotificationKindConfig)
class NotificationKindConfigAdmin(
    BaseModelAdmin, TabbedTranslationAdmin[NotificationKindConfig]
):
    """Per-action channel + message config: THE operator surface for "this
    action sends on these channels, saying this".

    One row per catalog kind, seeded by migration (a new kind ships its
    row in a data migration) - the kind set is the catalog's, never the
    operator's, so add/delete stay off. TabbedTranslationAdmin swaps
    ``title``/``body`` for their ar/en tabs, and FieldPermissions rules
    auto-cover the shadows, so the ANNOUNCEMENT message lock holds on both.
    """

    can_add = False  # one row per catalog kind, born from a migration
    can_change = True
    can_delete = False  # a deleted row = label-only, inbox-only sends
    field_permissions = FieldPermissions(
        readonly_when={
            # Rules auto-cover the modeltranslation _ar/_en shadow columns.
            "title": message_locked,
            "body": message_locked,
        },
    )
    resource_classes = [NotificationKindConfigResource]

    form = KindConfigAdminForm
    list_display = ("kind", "channels_display", "updated_at")
    list_filter = ()
    list_filter_submit = False
    search_fields = ()
    search_help_text = ""
    ordering = ("kind",)
    list_per_page = 50

    # TabbedTranslationAdmin is a typed base: name the TypedDict shape here.
    fieldsets: ClassVar[_FieldsetSpec] = (
        (None, {"fields": ("kind", "channels")}),
        (
            _("Message"),
            {
                "fields": ("placeholders", "title", "body", "preview"),
                "description": _(
                    "Write literal text with {key} placeholders from the list "
                    "below; both languages are required."
                ),
            },
        ),
    )
    readonly_fields = ("kind", "placeholders", "preview")  # kind: the row's identity

    @display(description=_("Channels"))
    def channels_display(self, obj: NotificationKindConfig) -> str:
        return ", ".join(obj.channels) if obj.channels else str(_("inbox only"))

    @display(description=_("Placeholders"))
    def placeholders(self, obj: NotificationKindConfig) -> str:
        """The kind's context keys, each with the example the preview uses."""
        entry = catalog_entry(NotificationKind(obj.kind))
        if not entry.context_keys:
            return str(_("This action has no placeholders."))
        return format_html_join(
            ", ",
            "<code>{{{}}}</code> ({})",
            ((key, SAMPLE_VALUES[key]) for key in sorted(entry.context_keys)),
        )

    @display(description=_("Preview"))
    def preview(self, obj: NotificationKindConfig) -> str:
        """The saved copy rendered with the sample values, per language."""
        kind = NotificationKind(obj.kind)
        entry = catalog_entry(kind)
        context = {key: SAMPLE_VALUES[key] for key in entry.context_keys}
        panes = []
        for code, label in (("en", _("English")), ("ar", _("Arabic"))):
            with translation.override(code):
                message = selectors.notification_render(
                    kind=kind, context=context, configs={kind: obj}
                )
            panes.append((label, message.title, message.body))
        return format_html_join(
            "",
            '<p dir="auto"><strong>{}</strong>: {} - {}</p>',
            panes,
        ) or format_html("")

    def save_model(
        self, request: HttpRequest, obj: Model, form: ModelForm[Any], change: bool
    ) -> None:
        """Every write goes through the service (the single writer): channels
        from the picker, copy from the translation tabs when editable."""
        config = cast("NotificationKindConfig", obj)
        copy = {
            field: form.cleaned_data[field]
            for field in MESSAGE_FIELDS
            if field in form.cleaned_data
        }
        services.notification_config_update(
            kind=NotificationKind(config.kind),
            channels=form.cleaned_data["channels"],
            **copy,
        )
