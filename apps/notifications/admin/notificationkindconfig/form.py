"""Per-card form for the single-page config editor.

One class both RENDERS a card (unbound, from the row) and VALIDATES its save
(bound, in the JSON endpoint) - the screen and the write path cannot drift.

The message fields are the modeltranslation shadow columns directly; the base
title/body columns follow the active language on save (descriptor write).
authored_per_send kinds (the composer's ANNOUNCEMENT) drop the message fields
entirely - their copy is written per broadcast, not here.
"""

from collections.abc import Mapping
from typing import Any

from django import forms
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from apps.notifications.admin.broadcast.form import CHANNEL_HINTS
from apps.notifications.admin.broadcast.form import CHANNEL_ORDER
from apps.notifications.catalog import MessageTemplate
from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationKindConfig
from apps.notifications.models.kind_config import MESSAGE_FIELDS

# One realistic example per catalog context key: the editor's preview renders
# the message with these instead of raw {placeholders}, and each insert chip
# shows its example as a tooltip. A new context key needs a sample here in the
# same change (test_config_admin enforces the pairing).
SAMPLE_VALUES: Mapping[str, str] = {
    "name": "Omar",
    "amount": "150.00",
    "currency": "SAR",
    "balance": "1,250.00",
    "title": "Weekend offer",
    "message": "20% off everything until Sunday.",
}


class KindConfigForm(forms.ModelForm[NotificationKindConfig]):
    channels = forms.MultipleChoiceField(
        label=_("Channels"),
        required=False,  # everything off = inbox-only, a legitimate policy
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = NotificationKindConfig
        fields = ("channels", *MESSAGE_FIELDS)
        labels = {
            "title_ar": _("Title (Arabic)"),
            "title_en": _("Title (English)"),
            "body_ar": _("Body (Arabic)"),
            "body_en": _("Body (English)"),
        }
        # The real form fields are the transport: the token editor hydrates
        # from them and writes back into them; CSS hides them (.nc-src).
        widgets = {
            "title_ar": forms.TextInput(attrs={"class": "nc-src", "dir": "rtl"}),
            "title_en": forms.TextInput(attrs={"class": "nc-src", "dir": "ltr"}),
            "body_ar": forms.Textarea(
                attrs={"class": "nc-src", "rows": 2, "dir": "rtl"}
            ),
            "body_en": forms.Textarea(
                attrs={"class": "nc-src", "rows": 2, "dir": "ltr"}
            ),
        }

    def __init__(self, *args: Any, kind: NotificationKind, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.kind = kind
        self.entry: MessageTemplate = catalog_entry(kind)
        if self.instance._state.adding:
            # A save that arrives before the page ever created the row: the
            # kind is not a form field, so set it here for the model's clean().
            self.instance.kind = kind
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

    def languages(self) -> list[dict[str, Any]]:
        """One composer pane per language: its title/body bound fields."""
        if self.entry.authored_per_send:
            return []
        return [
            {
                "code": "en",
                "label": _("English"),
                "dir": "ltr",
                "title": self["title_en"],
                "body": self["body_en"],
            },
            {
                "code": "ar",
                "label": _("Arabic"),
                "dir": "rtl",
                "title": self["title_ar"],
                "body": self["body_ar"],
            },
        ]

    @classmethod
    def starting_values(cls, kind: NotificationKind) -> dict[str, Any]:
        """The recommended row for a kind that has none: the catalog's
        starting copy (English in BOTH language columns - operators
        localize) and channels. Prefilled on the actions page; the row is
        written by the card's first save."""
        entry = catalog_entry(kind)
        with translation.override("en"):
            title, body = str(entry.title), str(entry.body)
        return {
            "channels": sorted(str(channel) for channel in entry.default_channels),
            "title_ar": title,
            "title_en": title,
            "body_ar": body,
            "body_en": body,
        }

    def variables(self) -> list[dict[str, str]]:
        """The kind's placeholders as insert chips: token + example value."""
        return [
            {"key": key, "token": f"{{{key}}}", "sample": SAMPLE_VALUES[key]}
            for key in sorted(self.entry.context_keys)
        ]

    def whatsapp_note(self) -> str:
        if Channel.WHATSAPP not in self.entry.supported_channels:
            return ""
        return str(
            _('WhatsApp sends the Meta-approved template "%(name)s".')
            % {"name": self.entry.whatsapp_template.name}
        )

    def service_kwargs(self) -> dict[str, Any]:
        """The save endpoint's payload for services.notification_config_update."""
        cleaned = self.cleaned_data
        kwargs: dict[str, Any] = {"kind": self.kind, "channels": cleaned["channels"]}
        for field in MESSAGE_FIELDS:
            if field in self.fields:
                kwargs[field] = cleaned[field]
        return kwargs
