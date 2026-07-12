"""Templated multipart email - how first-party code sends mail.

One HTML template is the single source of copy; the plain-text alternative
derives from it (style blocks stripped) so the two parts never drift.
Outside a request (task workers) wrap calls in
``translation.override(user.language)`` - there is no request locale there.
"""

import re
from typing import Any

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

_STYLE_BLOCK = re.compile(r"<style.*?</style>", re.DOTALL | re.IGNORECASE)
_EXCESS_BLANK_LINES = re.compile(r"\n\s*\n\s*\n+")


def email_send(
    *,
    subject: str,
    recipient_list: list[str],
    template_name: str,
    context: dict[str, Any] | None = None,
) -> None:
    html_body = render_to_string(template_name, context or {})
    text_body = _EXCESS_BLANK_LINES.sub(
        "\n\n", strip_tags(_STYLE_BLOCK.sub("", html_body))
    ).strip()
    message = EmailMultiAlternatives(subject=subject, body=text_body, to=recipient_list)
    message.attach_alternative(html_body, "text/html")
    message.send()
