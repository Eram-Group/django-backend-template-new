"""Per-card form for the single-page config editor.

One class both RENDERS a card (unbound, from the row) and VALIDATES its save
(bound, in the JSON endpoint) - the screen and the write path cannot drift.

The message fields are the modeltranslation shadow columns directly; the base
title/body columns follow the active language on save (descriptor write).
authored_per_send kinds (the composer's ANNOUNCEMENT) drop the message fields
entirely - their copy is written per broadcast, not here.
"""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.notifications.admin.broadcast.form import CHANNEL_HINTS
from apps.notifications.admin.broadcast.form import CHANNEL_ORDER
from apps.notifications.catalog import MessageTemplate
from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationKindConfig

MESSAGE_FIELDS = ("title_ar", "title_en", "body_ar", "body_en")


class KindConfigForm(forms.ModelForm[NotificationKindConfig]):
    channels = forms.MultipleChoiceField(
        label=_("Channels"),
        required=False,  # everything off = inbox-only, a legitimate policy
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = NotificationKindConfig
        fields = (
            "channels",
            "title_ar",
            "title_en",
            "body_ar",
            "body_en",
        )
        labels = {
            "title_ar": _("Title (Arabic)"),
            "title_en": _("Title (English)"),
            "body_ar": _("Body (Arabic)"),
            "body_en": _("Body (English)"),
        }
        widgets = {
            "title_ar": forms.TextInput(attrs={"class": "nc-input", "dir": "rtl"}),
            "title_en": forms.TextInput(attrs={"class": "nc-input", "dir": "ltr"}),
            "body_ar": forms.Textarea(
                attrs={"class": "nc-textarea", "rows": 2, "dir": "rtl"}
            ),
            "body_en": forms.Textarea(
                attrs={"class": "nc-textarea", "rows": 2, "dir": "ltr"}
            ),
        }

    def __init__(self, *args: Any, kind: NotificationKind, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.kind = kind
        self.entry: MessageTemplate = catalog_entry(kind)
        supported = self.entry.supported_channels
        self.fields["channels"].choices = [  # type: ignore[attr-defined]
            (str(channel), str(channel.label))
            for channel in CHANNEL_ORDER
            if channel in supported
        ]
        if self.entry.authored_per_send:
            for field in MESSAGE_FIELDS:
                del self.fields[field]

    def channel_rows(self) -> list[dict[str, Any]]:
        """Every channel in display order - real checkbox when supported,
        an inert disabled pill otherwise (the card shows the full picture).
        Hints render as tooltips, so the WhatsApp template note rides along."""
        widgets = {widget.data["value"]: widget for widget in self["channels"]}
        rows: list[dict[str, Any]] = []
        for channel in CHANNEL_ORDER:
            widget = widgets.get(str(channel))
            if widget is None:
                hint = str(_("Not available for this action."))
            else:
                hint = str(CHANNEL_HINTS.get(channel, ""))
                if channel == Channel.WHATSAPP:
                    hint = f"{hint} {self.whatsapp_note()}".strip()
            rows.append(
                {
                    "supported": widget is not None,
                    "tag": widget.tag() if widget is not None else "",
                    "label": str(channel.label),
                    "hint": hint,
                    "value": str(channel),
                }
            )
        return rows

    def message_pairs(self) -> list[dict[str, Any]]:
        """(arabic, english) bound-field pairs for the card's message block."""
        if self.entry.authored_per_send:
            return []
        return [
            {"arabic": self["title_ar"], "english": self["title_en"]},
            {"arabic": self["body_ar"], "english": self["body_en"]},
        ]

    def whatsapp_note(self) -> str:
        if Channel.WHATSAPP not in self.entry.supported_channels:
            return ""
        template = self.entry.whatsapp
        if template is None:  # unreachable while catalog __post_init__ holds
            return ""
        return str(
            _('WhatsApp sends the Meta-approved template "%(name)s".')
            % {"name": template.name}
        )

    def service_kwargs(self) -> dict[str, Any]:
        """The save endpoint's payload for services.notification_config_update."""
        cleaned = self.cleaned_data
        kwargs: dict[str, Any] = {
            "kind": self.kind,
            "channels": cleaned.get("channels") or [],
        }
        for field in MESSAGE_FIELDS:
            if field in self.fields:
                kwargs[field] = cleaned.get(field)
        return kwargs
