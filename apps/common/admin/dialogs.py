"""Confirmation dialogs for state-changing detail actions.

An unfold detail action is a plain URL; without a dialog the body runs on
GET, so a link prefetch, a history restore or an unfurl could start a refund
or a broadcast. With ``dialog=`` unfold renders a CSRF-protected form on GET
and calls the action only on a valid POST - the action body then receives
the bound form as its second argument.
"""

from typing import cast

from django_stubs_ext import StrOrPromise
from unfold.dataclasses import ActionDialog


def confirm_dialog(
    *, title: StrOrPromise, description: StrOrPromise, submit: StrOrPromise
) -> ActionDialog:
    """A yes/no dialog: GET shows ``title``/``description``, POST runs the
    action. Lazy strings resolve in the viewer's language at render time."""
    return ActionDialog(
        title=cast("str", title),
        description=cast("str", description),
        form_class=None,  # unfold's BaseDialogForm: one hidden confirm field
        form_submit_text=cast("str", submit),
    )
