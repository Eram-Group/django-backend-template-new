"""Admin forms for notifications.

Broadcast composer (``BroadcastComposeForm``): Compose form for the Broadcast add view.

The first form class in this project, and deliberately narrow: it exists so an
operator writes a title and a message instead of hand-typing the ``context``
JSON, and so the add path can hand real values to
``services.notification_broadcast`` - which is what makes the catalog's
context validation run at all.

Add view only. On change, ``FieldPermissions`` freezes content and audience
(a dispatched broadcast must show what was actually sent), so the change form
stays the plain auto-built one.

Kind config (``KindConfigForm``): Per-card form for the single-page config editor.

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

from apps.notifications.catalog import MessageTemplate
from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Broadcast
from apps.notifications.models import NotificationKindConfig
from apps.notifications.models.kind_config import MESSAGE_FIELDS
from apps.users.models import User

# Only ANNOUNCEMENT is operator-authored: its title and body are literally
# "{title}" and "{message}". The other kinds are per-user events - WELCOME
# needs that user's name, WALLET_CREDITED their own amount and balance - so one
# shared context would show every recipient the same wrong numbers. Restricting
# the choice makes that impossible rather than merely discouraged.
COMPOSABLE_KIND = NotificationKind.ANNOUNCEMENT

# Advisory, not enforced: every platform truncates at its own width and none of
# them reject a longer string. The counters colour past these; nothing blocks.
TITLE_LIMIT = 65
MESSAGE_LIMIT = 240

# Ordered, because the catalog holds a frozenset and its iteration order is not
# stable across processes - unordered choices would reshuffle between requests.
CHANNEL_ORDER = (Channel.PUSH, Channel.SMS, Channel.WHATSAPP)

CHANNEL_HINTS = {
    Channel.PUSH: _("Every registered device."),
    Channel.SMS: _("Billed per message."),
    Channel.WHATSAPP: _("Sent through the approved template."),
}


def _channel_choices() -> list[tuple[str, str]]:
    supported = catalog_entry(COMPOSABLE_KIND).supported_channels
    return [
        (str(channel), str(channel.label))
        for channel in CHANNEL_ORDER
        if channel in supported
    ]


DATE_ATTRS = {
    "type": "text",
    "class": "bc-input bc-date",
    "inputmode": "numeric",
    "placeholder": "YYYY-MM-DD",
    "autocomplete": "off",
    "maxlength": "10",
}


TARGET_FILTERS = "filters"
TARGET_USERS = "users"


class BroadcastAudienceForm(forms.ModelForm[Broadcast]):
    """Just the audience: the filters, or a hand-picked list of users.

    Split out so the live reach estimate can validate a half-written audience
    without demanding the message the operator has not typed yet.
    """

    target = forms.ChoiceField(
        label=_("Send to"),
        choices=[
            (TARGET_FILTERS, _("Everyone matching the filters")),
            (TARGET_USERS, _("Specific users")),
        ],
        initial=TARGET_FILTERS,
        required=False,  # absent (an estimate with no filters yet) = filters
        widget=forms.RadioSelect,
    )
    # Posted as repeated hidden inputs by the composer's user picker; the
    # queryset is the validation (an inactive or unknown pk is a field error).
    recipients = forms.ModelMultipleChoiceField(
        label=_("Users"),
        queryset=User.objects.filter(is_active=True),
        required=False,
        widget=forms.MultipleHiddenInput,
    )

    class Meta:
        model = Broadcast
        fields = (
            "language",
            "require_device",
            "joined_after",
            "joined_before",
        )
        widgets = {
            # Radios rather than a <select>: the composer renders them as
            # chips, and "no filter" has to be a visible, clickable option
            # instead of an empty row that reads as "nothing chosen yet".
            "language": forms.RadioSelect,
            # ISO text fields: the composer's own calendar (broadcast_compose.js)
            # fills them, typing still works, and the value posted is the same
            # YYYY-MM-DD the form always accepted - no browser-native popover.
            "joined_after": forms.DateInput(attrs=DATE_ATTRS, format="%Y-%m-%d"),
            "joined_before": forms.DateInput(attrs=DATE_ATTRS, format="%Y-%m-%d"),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        language = self.fields["language"]
        # ModelForm gives a blank choice labelled "---------"; name it.
        language.choices = [  # type: ignore[attr-defined]
            ("", _("All languages")),
            *[choice for choice in language.choices if choice[0]],  # type: ignore[attr-defined]
        ]

    def clean(self) -> dict[str, Any]:
        super().clean()
        cleaned = self.cleaned_data
        if (cleaned.get("target") or TARGET_FILTERS) == TARGET_USERS:
            if not cleaned.get("recipients"):
                self.add_error("recipients", _("Pick at least one user."))
        else:
            cleaned["recipients"] = []  # a leftover pick must not narrow "everyone"
        joined_after = cleaned.get("joined_after")
        joined_before = cleaned.get("joined_before")
        if joined_after and joined_before and joined_after > joined_before:
            # Caught here as well as in the service so the operator gets a
            # field error rather than a page-level message.
            self.add_error(
                "joined_before",
                _("Must be on or after the joined-after date."),
            )
        return cleaned

    def audience_preview(self) -> Broadcast:
        """An unsaved Broadcast carrying just the filters.

        Fed to ``selectors.broadcast_audience`` so the estimate is computed by
        the very query the dispatcher will page - an estimate from a parallel
        implementation could disagree with what actually sends.
        """
        cleaned = self.cleaned_data
        return Broadcast(
            kind=COMPOSABLE_KIND,
            language=cleaned["language"],
            require_device=cleaned["require_device"],
            joined_after=cleaned["joined_after"],
            joined_before=cleaned["joined_before"],
            recipient_ids=self.recipient_ids(),
        )

    def recipient_ids(self) -> list[str]:
        return [str(user.pk) for user in self.cleaned_data.get("recipients", [])]

    def selected_users(self) -> list[dict[str, str]]:
        """The picked users for re-rendering the chips after a failed POST."""
        raw = self.data.getlist("recipients") if hasattr(self.data, "getlist") else []
        if not raw:
            return []
        return [
            {"id": str(user.pk), "name": user.name, "email": user.email}
            for user in User.objects.filter(is_active=True, pk__in=raw).order_by("name")
        ]


class BroadcastComposeForm(BroadcastAudienceForm):
    """Author an announcement: title + body in, a rendered ``context`` out."""

    title = forms.CharField(
        label=_("Title"),
        max_length=255,
        widget=forms.TextInput(attrs={"class": "bc-input", "autofocus": True}),
        help_text=_("The headline recipients see in the notification tray."),
    )
    message = forms.CharField(
        label=_("Body"),
        widget=forms.Textarea(attrs={"class": "bc-textarea", "rows": 4}),
        help_text=_("The text every recipient sees. Sent as-is, in one piece."),
    )
    channels = forms.MultipleChoiceField(
        label=_("Channels"),
        widget=forms.CheckboxSelectMultiple,
        help_text=_(
            "Where this broadcast goes out. The in-app inbox entry is always written."
        ),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Resolved per-instance, not at import: the catalog is code, but a
        # module-level call would freeze the choices before app loading.
        self.fields["channels"].choices = _channel_choices()  # type: ignore[attr-defined]

    def channel_rows(self) -> list[dict[str, Any]]:
        """Each channel checkbox paired with its cost/behaviour hint.

        Assembled here rather than in the template: a template would need a
        custom filter to look a hint up by value, and the hints belong next to
        the choices they describe.
        """
        return [
            {
                "tag": widget.tag(),
                "label": widget.choice_label,
                "value": widget.data["value"],
                "hint": CHANNEL_HINTS.get(Channel(widget.data["value"]), ""),
            }
            for widget in self["channels"]
        ]

    def service_kwargs(self) -> dict[str, Any]:
        """The add path's payload for ``services.notification_broadcast``.

        Only valid after ``is_valid()``: every field is then present in
        ``cleaned_data`` (blank optional fields as ""/None/False/[]).
        """
        cleaned = self.cleaned_data
        return {
            "kind": COMPOSABLE_KIND,
            "context": {"title": cleaned["title"], "message": cleaned["message"]},
            "language": cleaned["language"],
            "require_device": cleaned["require_device"],
            "joined_after": cleaned["joined_after"],
            "joined_before": cleaned["joined_before"],
            "channels": cleaned["channels"],
            "recipient_ids": self.recipient_ids(),
        }


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
