"""Allauth account adapter.

Project rule (no signals): allauth hands a completed signup to this
adapter's save_user, which creates the user through the ONE signup service
(``user_create``) - never the user_signed_up signal. A social adapter, when
built, calls ``user_create`` the same way with the provider's profile.

Skip-OTP-for-social needs no override here: ACCOUNT_LOGIN_BY_CODE_REQUIRED
uses allauth's method-list semantics in settings (verified in 65.18 source).
"""

from typing import Any

from allauth.account import app_settings as account_settings
from allauth.account.adapter import DefaultAccountAdapter
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.utils.crypto import get_random_string
from django.utils.translation import get_language_from_request

from apps.common.emails import email_send
from apps.users.constants import Language
from apps.users.models import User
from apps.users.services import user_create

CODE_LENGTH = 6
CODE_ALPHABET = "0123456789"


class AccountAdapter(DefaultAccountAdapter):
    def generate_login_code(self) -> str:
        """6-digit numeric login codes (allauth default is dashed alphanumeric)."""
        return get_random_string(length=CODE_LENGTH, allowed_chars=CODE_ALPHABET)

    def generate_email_verification_code(self) -> str:
        """6-digit numeric email verification codes."""
        return get_random_string(length=CODE_LENGTH, allowed_chars=CODE_ALPHABET)

    def send_mail(self, template_prefix: str, email: str, context: Any) -> None:
        """Every allauth email goes out through the project's one renderer
        (``email_send``: multipart from the HTML template, site branding);
        allauth keeps the subject template + prefix. The login-code template
        renders its expiry from the real setting - hardcoded copy drifts
        (learned from the reference template)."""
        if template_prefix == "account/email/login_code":
            context = {
                **context,
                "code_ttl_minutes": account_settings.LOGIN_BY_CODE_TIMEOUT // 60,
            }
        subject = render_to_string(f"{template_prefix}_subject.txt", context)
        email_send(
            subject=self.format_email_subject(" ".join(subject.splitlines()).strip()),
            recipient_list=[email],
            template_name=f"{template_prefix}_message.html",
            context=context,
        )

    def save_user(
        self,
        request: HttpRequest,
        user: User,
        form: Any,
        commit: bool = True,
    ) -> User:
        """The signup form is complete: create the user through the service.

        allauth keeps using ``user`` after this call (email setup, the code
        flow), so the service fills and saves that very instance.
        First-touchpoint localization: the welcome email renders in
        user.language, so the client's Accept-Language (constrained to
        LANGUAGES by Django) is captured now.
        """
        if not commit:
            msg = "social signup must call apps.users.services.user_create itself"
            raise NotImplementedError(msg)
        return user_create(
            user=user,
            email=form.cleaned_data["email"],
            name=form.cleaned_data["name"],
            language=Language(get_language_from_request(request)),
        )
