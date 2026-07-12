"""Inline for embedding Wallet rows on a parent admin.

Decide: does Wallet appear inline anywhere? If yes, fill this in and add
it to the parent admin's `inlines`; if not, delete the file and tick the
CHECKLIST line either way.
"""

from apps.common.admin import BaseTabularInline
from apps.payments.models import Wallet


class WalletInline(BaseTabularInline):
    """Wallet rows under a parent's change form."""

    model = Wallet
    can_add = False  # decide deliberately, then flip
    can_change = False
    can_delete = False
    fields = ()  # explicit, like the resource
    show_on_add = False  # inlines hide on the parent's add view by default
    # Display-only rows? subclass ReadOnlyTabularInline instead.
