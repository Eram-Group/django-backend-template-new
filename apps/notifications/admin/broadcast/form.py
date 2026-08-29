"""Compose form for the Broadcast add view.

The first form class in this project, and deliberately narrow: it exists so an
operator writes a title and a message instead of hand-typing the ``context``
JSON, and so the add path can hand real values to
``services.notification_broadcast`` - which is what makes the catalog's
context validation run at all.

Add view only. On change, ``FieldPermissions`` freezes content and audience
(a dispatched broadcast must show what was actually sent), so the change form
stays the plain auto-built one.
"""

from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Broadcast

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


class BroadcastAudienceForm(forms.ModelForm[Broadcast]):
    """Just the audience filters.

    Split out so the live reach estimate can validate a half-written audience
    without demanding the message the operator has not typed yet.
    """

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
            "joined_after": forms.DateInput(
                attrs={"type": "date", "class": "bc-input"}
            ),
            "joined_before": forms.DateInput(
                attrs={"type": "date", "class": "bc-input"}
            ),
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
        )


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
        }
