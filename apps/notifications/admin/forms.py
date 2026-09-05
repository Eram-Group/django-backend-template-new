"""Admin forms for notifications - plain Django forms on unfold widgets.

``BroadcastComposeForm`` (the Broadcast add view, laid out by
``templates/admin/notifications/broadcast/compose.html``): an operator
writes a title and a message instead of hand-typing the ``context`` JSON,
picks an audience and the channels, and the add path hands real values to
``services.notification_broadcast`` - which is what makes the catalog's
context validation run at all. Its audience half (``BroadcastAudienceForm``)
is also what the live reach counter validates. Add view only: on change,
``FieldPermissions`` freezes content and audience (a dispatched broadcast
must show what was actually sent), so the change form stays the plain
auto-built one. The ``x-model.fill`` attrs feed the page's Alpine state
(counters, preview, the Everyone/Specific users switch).

``KindConfigAdminForm`` (the NotificationKindConfig change view): the
channel picker limited to what the kind supports; the copy columns are the
modeltranslation tabs the admin builds on its own.
"""

from collections.abc import Mapping
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _
from unfold.widgets import UnfoldAdminCheckboxSelectMultipleWidget
from unfold.widgets import UnfoldAdminRadioSelectWidget
from unfold.widgets import UnfoldAdminSingleDateWidget
from unfold.widgets import UnfoldAdminTextareaWidget
from unfold.widgets import UnfoldAdminTextInputWidget
from unfold.widgets import UnfoldBooleanSwitchWidget

from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Broadcast
from apps.notifications.models import NotificationKindConfig

# Only ANNOUNCEMENT is operator-authored: its title and body are literally
# "{title}" and "{message}". The other kinds are per-user events - WELCOME
# needs that user's name, WALLET_CREDITED their own amount and balance - so one
# shared context would show every recipient the same wrong numbers. Restricting
# the choice makes that impossible rather than merely discouraged.
COMPOSABLE_KIND = NotificationKind.ANNOUNCEMENT

# Advisory, not enforced: every platform truncates at its own width and none of
# them reject a longer string. The help text names the widths; nothing blocks.
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

# One realistic example per catalog context key: the config change form's
# preview renders the message with these instead of raw {placeholders}. A
# new context key needs a sample here in the same change (test_config_admin
# enforces the pairing).
SAMPLE_VALUES: Mapping[str, str] = {
    "name": "Omar",
    "amount": "150.00",
    "currency": "SAR",
    "balance": "1,250.00",
    "title": "Weekend offer",
    "message": "20% off everything until Sunday.",
}

TARGET_FILTERS = "filters"
TARGET_USERS = "users"


def _channel_choices(supported: frozenset[Channel]) -> list[tuple[str, str]]:
    """Display-ordered choices, each labelled with its hint."""
    return [
        (str(channel), f"{channel.label} - {CHANNEL_HINTS[channel]}")
        for channel in CHANNEL_ORDER
        if channel in supported
    ]


class BroadcastAudienceForm(forms.ModelForm[Broadcast]):
    """The audience half of the composer - also what the live reach counter
    validates, so the number on screen comes from the same rules as the send."""

    target = forms.ChoiceField(
        label=_("Send to"),
        choices=(
            (TARGET_FILTERS, _("Everyone matching the filters")),
            (TARGET_USERS, _("Specific users")),
        ),
        initial=TARGET_FILTERS,
        widget=UnfoldAdminRadioSelectWidget(attrs={"x-model.fill": "target"}),
    )

    class Meta:
        model = Broadcast
        # ``recipients`` stays a model field so the admin's autocomplete
        # widget (autocomplete_fields) drives the user picker.
        fields = (
            "recipients",
            "language",
            "require_device",
            "joined_after",
            "joined_before",
        )
        widgets = {
            "language": UnfoldAdminRadioSelectWidget,
            "require_device": UnfoldBooleanSwitchWidget,
            "joined_after": UnfoldAdminSingleDateWidget,
            "joined_before": UnfoldAdminSingleDateWidget,
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        language = self.fields["language"]
        language.required = False
        language.choices = [  # type: ignore[attr-defined]
            ("", _("All languages")),
            *[(code, label) for code, label in language.choices if code],  # type: ignore[attr-defined]
        ]
        self.fields["recipients"].required = False

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("target") == TARGET_USERS:
            if not cleaned.get("recipients"):
                self.add_error("recipients", _("Pick at least one user."))
        else:
            # A leftover pick must not narrow "everyone matching the filters".
            cleaned["recipients"] = []
        after, before = cleaned.get("joined_after"), cleaned.get("joined_before")
        if after and before and after > before:
            self.add_error(
                "joined_before", _("Must be on or after the joined-after date.")
            )
        return cleaned

    def audience_filters(self) -> dict[str, Any]:
        """The cleaned audience as ``selectors.audience_queryset`` kwargs."""
        cleaned = self.cleaned_data
        return {
            "language": cleaned["language"] or "",
            "require_device": cleaned["require_device"],
            "joined_after": cleaned["joined_after"],
            "joined_before": cleaned["joined_before"],
            "recipient_ids": [user.pk for user in cleaned["recipients"]],
        }


class BroadcastComposeForm(BroadcastAudienceForm):
    """Title + message -> ``context``, plus the channels."""

    title = forms.CharField(
        label=_("Title"),
        max_length=255,
        widget=UnfoldAdminTextInputWidget(
            attrs={"autofocus": True, "x-model.fill": "title"}
        ),
        help_text=_("Push notifications show about %(limit)d characters.")
        % {"limit": TITLE_LIMIT},
    )
    message = forms.CharField(
        label=_("Message"),
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 4, "x-model.fill": "message"}),
        help_text=_("SMS and push preview about %(limit)d characters.")
        % {"limit": MESSAGE_LIMIT},
    )
    channels = forms.MultipleChoiceField(
        label=_("Channels"),
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        help_text=_("Every recipient also gets the inbox entry."),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        channels = self.fields["channels"]
        channels.choices = _channel_choices(  # type: ignore[attr-defined]
            catalog_entry(COMPOSABLE_KIND).supported_channels
        )
        # unfold's checkbox widget rebuilds ``attrs`` in __init__ (class only),
        # so the Alpine binding is added afterwards.
        channels.widget.attrs["x-model.fill"] = "channels"

    def service_kwargs(self) -> dict[str, Any]:
        """The add path's payload for services.notification_broadcast."""
        cleaned = self.cleaned_data
        return {
            "kind": COMPOSABLE_KIND,
            "context": {"title": cleaned["title"], "message": cleaned["message"]},
            "language": cleaned["language"] or "",
            "require_device": cleaned["require_device"],
            "joined_after": cleaned["joined_after"],
            "joined_before": cleaned["joined_before"],
            "channels": cleaned["channels"],
            "recipients": list(cleaned["recipients"]),
        }


class KindConfigAdminForm(forms.ModelForm[NotificationKindConfig]):
    """The channel picker, limited to the kind's supported channels; the
    title/body tabs come from the admin (modeltranslation)."""

    channels = forms.MultipleChoiceField(
        label=_("Channels"),
        required=False,  # everything off = inbox-only, a legitimate policy
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        help_text=_("The inbox entry is always written; these are the extra channels."),
    )

    class Meta:
        model = NotificationKindConfig
        fields = ("channels",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        entry = catalog_entry(NotificationKind(self.instance.kind))
        self.fields["channels"].choices = _channel_choices(  # type: ignore[attr-defined]
            entry.supported_channels
        )
